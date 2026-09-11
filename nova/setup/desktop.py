from __future__ import annotations

import socket
import sys
import threading
import uuid
from dataclasses import asdict
from pathlib import Path

from nova.core.config import load_settings
from nova.core.logging import get_logger
from nova.desktop.configuration import edit_configuration
from nova.llm.ollama import OllamaProvider
from nova.setup.catalog import candidates
from nova.setup.dependencies import install_ollama, version_key
from nova.setup.detect import _find_ollama_bin, detect_disk_free_gb, detect_machine, ensure_ollama_running
from nova.setup.models import normalize_model_name
from nova.setup.ollama_lifecycle import local_endpoint, model_directory, snapshot
from nova.setup.selector import Availability, capability_profile, evaluate
from nova.setup.state import StateStore

logger = get_logger('setup.desktop')


def internet_available() -> bool:
    try:
        with socket.create_connection(('ollama.com', 443), timeout=3):
            return True
    except OSError:
        return False


def recommended_chat(profile):
    cap = capability_profile(profile)
    for state in (Availability.RECOMMENDED, Availability.POSSIBLE):
        for spec in candidates('general'):
            if evaluate(spec, cap)[0] is state:
                return spec
    raise ValueError('No hay suficiente memoria libre para un modelo local. Cierra otras aplicaciones y reintenta.')


class Preparation:
    """One observable, resumable preparation operation; no synthetic percentage."""

    def __init__(self, config: Path):
        self.config = config
        self._lock = threading.Lock()
        self._state = dict(id='', status='idle', stage='', message='', completed=0, total=None, checks=[])

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def emit(self, **values):
        with self._lock:
            self._state.update(values)

    def start(self, model: str | None = None) -> dict:
        with self._lock:
            if self._state['status'] == 'running':
                return dict(self._state)
            self._state = dict(id=uuid.uuid4().hex, status='running', stage='system',
                               message='Comprobando tu ordenador…', completed=0, total=None, checks=[])
        threading.Thread(target=self.run, args=(model,), daemon=True).start()
        return self.status()

    def stage(self, name: str, message: str):
        self.emit(stage=name, message=message, completed=0, total=None)

    def run(self, model: str | None = None):
        try:
            with StateStore().lock():
                self._prepare(model)
            self.emit(status='ready', stage='ready', message='N.O.V.A. está lista.', completed=0, total=None)
        except Exception as exc:
            logger.exception('Desktop preparation failed')
            self.emit(status='error', message=str(exc) if isinstance(exc, ValueError) else
                      'No hemos podido completar esta parte de la instalación. Comprueba tu conexión y reintenta.',
                      details=str(exc), total=None)

    def _prepare(self, model: str | None):
        settings = load_settings(str(self.config))
        endpoint = settings.llm.base_url
        if settings.llm.provider != 'ollama' or not local_endpoint(endpoint):
            raise ValueError('La preparación automática necesita un motor local en este ordenador.')
        profile = detect_machine()
        if sys.platform == 'win32' and (sys.getwindowsversion().build < 19045 or profile.arch.lower() not in ('amd64', 'x86_64')):
            raise ValueError('Esta edición necesita Windows 10 22H2 o Windows 11 de 64 bits (Intel/AMD).')
        spec = recommended_chat(profile)
        if model:
            spec = next((s for kind in ('general', 'vision', 'coding', 'reasoning', 'fast')
                         for s in candidates(kind) if s.name == model), None)
            if spec is None or evaluate(spec, capability_profile(profile))[0] is Availability.NOT_RECOMMENDED:
                raise ValueError('Ese modelo no es adecuado para los recursos disponibles en tu ordenador.')
        detected = snapshot(endpoint)
        name = normalize_model_name(spec.name)
        volume = model_directory()
        while not volume.exists() and volume != volume.parent:
            volume = volume.parent
        free = detect_disk_free_gb(str(volume))
        network = internet_available()
        needs_download = name not in detected.models
        checks = [dict(label='Windows compatible' if sys.platform == 'win32' else profile.os_name, ok=True),
                  dict(label=f'{profile.ram_total_gb:.0f} GB de memoria · modelo adecuado', ok=True),
                  dict(label=f'{free:.0f} GB de espacio disponible', ok=not needs_download or free >= spec.weights_gb + 12),
                  dict(label=profile.gpu_model or 'Procesador disponible · no necesitas una GPU', ok=True),
                  dict(label='Conexión disponible' if network else 'Sin conexión', ok=network or not needs_download)]
        self.emit(checks=checks, recommendation=dict(name=spec.name, size_gb=spec.weights_gb, parameters=spec.params_b),
                  hardware=asdict(profile))
        if needs_download and (free <= 0 or free < spec.weights_gb + 12):
            raise ValueError(f'Necesitas al menos {spec.weights_gb + 12:.0f} GB libres para preparar el modelo con margen.')
        if needs_download and not network:
            raise ValueError('Conéctate a Internet para la primera descarga. Después podrás chatear sin conexión.')
        self.stage('engine', 'Preparando el motor local…')
        existing_binary = _find_ollama_bin()
        if not detected.running:
            if existing_binary:
                ensure_ollama_running(endpoint, wait_s=30)
                detected = snapshot(endpoint)
            if not existing_binary and not detected.running:
                self.stage('components', 'Instalando el motor local…')
                if not install_ollama():
                    raise ValueError('No hemos podido instalar el motor local. Reintenta con conexión a Internet.')
                binary = _find_ollama_bin()
                if binary:
                    def own(state):
                        state.ollama.installed_by_nova = True
                        state.ollama.executable = binary
                        state.ollama.method = 'official'
                    StateStore().change(own)
                ensure_ollama_running(endpoint, wait_s=45)
                detected = snapshot(endpoint)
        if not detected.running:
            raise ValueError('N.O.V.A. no ha podido iniciar el motor local. Reintenta o abre Diagnóstico.')
        if version_key(detected.version) < version_key(spec.min_ollama or '0.6.0'):
            self.stage('components', 'Actualizando el motor local…')
            if not install_ollama():
                raise ValueError('No hemos podido actualizar el motor local. Reintenta.')
            ensure_ollama_running(endpoint, wait_s=45)
            detected = snapshot(endpoint)
            if version_key(detected.version) < version_key(spec.min_ollama or '0.6.0'):
                raise ValueError('El motor anterior sigue abierto. Reinicia N.O.V.A. y reintenta la preparación.')
        if name not in detected.models:
            self.stage('model', f'Descargando {spec.name}…')
            provider = OllamaProvider(settings.llm)
            try:
                final = provider.pull_model(spec.name, timeout=120,
                    progress=lambda done, total, status: self.emit(completed=done, total=total or None))
            finally:
                provider.close()
            if final != 'success':
                raise ValueError('La descarga no ha terminado. Pulsa Reintentar para continuar.')
            StateStore().record_model(spec.name, spec.role, endpoint, owned=True)
        self.stage('verify', 'Comprobando el modelo local…')
        verified = snapshot(endpoint)
        if not verified.running or name not in verified.models:
            raise ValueError('No hemos podido verificar el modelo. Reintenta para completar la descarga.')
        StateStore().record_model(spec.name, spec.role, endpoint,
                                 owned=name not in detected.models, digest=verified.models[name])
        self.stage('config', 'Guardando tus preferencias…')
        def commit(data):
            llm = data.setdefault('llm', {})
            catalog = llm.setdefault('models', {})
            if spec.role == 'general':
                llm['default_model'] = spec.name
                catalog.update(local=spec.name, small=spec.name)
                data.setdefault('desktop', {})['prepared'] = True
            else:
                catalog[spec.role] = spec.name
        edit_configuration(self.config, commit)
