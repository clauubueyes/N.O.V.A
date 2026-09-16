from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr

from nova import __version__
from nova.core.config import PermissionSettings, load_settings
from nova.core.paths import default_config_path
from nova.core.secrets import SecretStore, SecretStoreUnavailable
from nova.desktop.configuration import edit_configuration
from nova.desktop.tunnel import TunnelError
from nova.llm.base import NOVAProviderError
from nova.llm.router import build_router
from nova.setup.catalog import SPECS_BY_ROLE
from nova.setup.desktop import Preparation
from nova.setup.detect import detect_machine
from nova.setup.selector import Availability, capability_profile, evaluate
from nova.tools.permissions import TOOL_CATEGORIES


class AttachmentInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    data: str = Field(max_length=12 * 1024 * 1024)


class PreferenceInput(BaseModel):
    mode: Literal['private', 'balanced', 'advanced'] | None = None
    model: str | None = None
    onboarding_complete: bool | None = None
    categories: PermissionSettings | None = None
    roots: list[str] | None = Field(default=None, max_length=30)
    autostart: bool | None = None
    routing: RoutingPreferenceInput | None = None
    providers: list[ProviderPreferenceInput] | None = Field(default=None, max_length=12)


class RoutingPreferenceInput(BaseModel):
    policy: Literal['local', 'balanced', 'performance', 'custom']
    preferred_local_model: str = ""
    preferred_cloud_provider: str = ""
    preferred_cloud_model: str = ""
    maximum_context: int | None = Field(default=None, ge=1024)
    allow_cloud_fallback: bool = False
    require_cloud_confirmation: bool = False
    never_send_data_to_cloud: bool = False
    never_send_sensitive_data_to_cloud: bool = True
    redact_cloud_requests: bool = True


class ProviderPreferenceInput(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    enabled: bool = False
    base_url: str = ""
    model: str = ""
    api_key: SecretStr | None = Field(default=None, repr=False)
    api_key_env: str = ""
    location: Literal['local', 'cloud'] = 'cloud'


class PrepareInput(BaseModel):
    model: str | None = None


class ApprovalInput(BaseModel):
    decision: Literal['once', 'always', 'cancel']


class PauseInput(BaseModel):
    paused: bool


class TunnelInput(BaseModel):
    active: bool


def build_product_router(state, *, config_path: Path | None, desktop: bool) -> APIRouter:
    from nova.api.setup import _guard

    def guard(request: Request):
        _guard(request)

    router = APIRouter(prefix='/v1/desktop', tags=['desktop'], dependencies=[Depends(guard)])
    path = config_path or default_config_path()
    preparation = Preparation(path)

    def apply_preferences(fresh):
        current = state.settings
        current.llm.default_model = fresh.llm.default_model
        current.llm.models = fresh.llm.models
        current.desktop = fresh.desktop
        current.ai = fresh.ai
        current.model_router = fresh.model_router
        current.openai = fresh.openai
        current.gemini = fresh.gemini
        current.open_code = fresh.open_code
        current.openai_compatible = fresh.openai_compatible
        current.permissions.categories = fresh.permissions.categories
        current.host.roots = fresh.host.roots
        from nova.llm.orchestrator import InferenceOrchestrator
        from nova.llm.registry import create_providers
        previous = state.providers
        state.providers = create_providers(current, local_provider=state.provider)
        state.cloud_provider = next((p for p in state.providers.values() if p.location == 'cloud'), None)
        state.inference = InferenceOrchestrator(
            state.providers, local_provider=current.llm.provider, audit=state.audit
        )
        for managed in previous.values():
            if managed is not state.provider and managed not in state.providers.values():
                managed.close()
        state.router = build_router(
            current.llm, current.model_router, ai_mode=current.ai.mode,
            ai_privacy=current.ai.privacy, open_code=current.open_code,
            openai=current.openai, gemini=current.gemini,
            compatible=current.openai_compatible,
        )
        if state.approvals:
            state.approvals.cancel_all()

    @router.get('/status')
    def status():
        settings = state.settings
        tunnel = state.tunnel
        if tunnel and tunnel.active:
            remote = dict(enabled=True, url=tunnel.url, message='Enlace público activo. Comparte el enlace desde Ajustes → Compartir acceso.')
        else:
            remote = dict(enabled=False, url=None, message='El acceso desde otros dispositivos no está activado. Puedes activarlo desde Ajustes → Compartir acceso.')
        return dict(version=__version__, desktop=desktop, paused=state.paused,
                    onboarding_complete=settings.desktop.onboarding_complete,
                    prepared=settings.desktop.prepared, mode=settings.desktop.mode,
                    model=settings.llm.default_model, privacy=settings.ai.privacy,
                    categories=settings.permissions.categories, roots=settings.host.roots,
                    remote=remote, voice=False,
                    routing={
                        'policy': settings.model_router.policy,
                        'preferred_local_model': settings.model_router.preferred_local_model,
                        'preferred_cloud_provider': settings.model_router.preferred_cloud_provider,
                        'preferred_cloud_model': settings.model_router.preferred_cloud_model,
                        'maximum_context': settings.model_router.maximum_context,
                        'allow_cloud_fallback': settings.model_router.allow_cloud_fallback,
                        'require_cloud_confirmation': settings.model_router.require_cloud_confirmation,
                        'never_send_data_to_cloud': settings.model_router.never_send_data_to_cloud,
                        'never_send_sensitive_data_to_cloud': settings.model_router.never_send_sensitive_data_to_cloud,
                        'redact_cloud_requests': settings.model_router.redact_cloud_requests,
                    },
                    ai_providers=[
                        {
                            'name': settings.llm.provider,
                            'enabled': True,
                            'base_url': settings.llm.base_url,
                            'model': settings.llm.default_model,
                            'api_key_env': '',
                            'credential_configured': True,
                            'location': 'local',
                            'healthy': state.providers[settings.llm.provider].health(),
                        },
                    ] + [
                        {
                            'name': name,
                            'enabled': configured.enabled,
                            'base_url': configured.base_url,
                            'model': configured.default_model,
                            'api_key_env': getattr(configured, 'api_key_env', ''),
                            'credential_configured': bool(getattr(configured, 'resolve_api_key', lambda: '')()),
                            'location': 'cloud',
                            'healthy': state.providers[name].health() if name in state.providers else False,
                        }
                        for name, configured in (
                            ('openai', settings.openai), ('gemini', settings.gemini),
                            ('opencode', settings.open_code),
                        )
                    ] + [
                        {
                            'name': configured.name,
                            'enabled': configured.enabled,
                            'base_url': configured.base_url,
                            'model': configured.default_model,
                            'api_key_env': configured.api_key_env,
                            'credential_configured': bool(configured.resolve_api_key()),
                            'location': configured.location,
                            'healthy': state.providers[configured.name].health() if configured.name in state.providers else False,
                        }
                        for configured in settings.openai_compatible
                    ])

    @router.patch('/preferences')
    def preferences(payload: PreferenceInput):
        if payload.model:
            from nova.setup.models import normalize_model_name
            installed = {normalize_model_name(m.name) for m in state.provider.list_models()}
            if normalize_model_name(payload.model) not in installed:
                raise HTTPException(400, 'Descarga el modelo antes de seleccionarlo.')
        if payload.roots is not None:
            for root in payload.roots:
                if not Path(root).is_absolute() or not Path(root).is_dir():
                    raise HTTPException(400, 'Selecciona carpetas existentes con su ubicación completa.')
        def change(data):
            if payload.mode is not None:
                data.setdefault('desktop', {})['mode'] = payload.mode
                data.setdefault('ai', {}).update(mode='local', privacy='local_only')
                if payload.mode == 'balanced' and state.settings.open_code.enabled:
                    data['ai'].update(mode='hybrid', privacy='cloud_allowed')
            if payload.onboarding_complete is not None:
                data.setdefault('desktop', {})['onboarding_complete'] = payload.onboarding_complete
            if payload.model:
                data.setdefault('llm', {})['default_model'] = payload.model
                data['llm'].setdefault('models', {}).update(local=payload.model, small=payload.model)
            if payload.categories is not None:
                categories = payload.categories.categories
                if categories.get('remote', 'deny') != 'deny' or categories.get('system', 'deny') != 'deny':
                    raise ValueError('Las acciones administrativas y remotas permanecen bloqueadas en esta edición.')
                data.setdefault('permissions', {})['categories'] = categories
            if payload.roots is not None:
                data.setdefault('host', {})['roots'] = payload.roots
            if payload.routing is not None:
                data['model_router'] = {
                    **data.get('model_router', {}),
                    **payload.routing.model_dump(),
                }
                if payload.routing.never_send_data_to_cloud or payload.routing.policy == 'local':
                    data.setdefault('ai', {}).update(mode='local', privacy='local_only')
                else:
                    data.setdefault('ai', {}).update(mode='hybrid', privacy='cloud_allowed')
            if payload.providers is not None:
                custom = []
                for item in payload.providers:
                    if item.base_url:
                        parsed = urlsplit(item.base_url)
                        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                            raise ValueError(f'Dirección no válida para {item.name}.')
                    secret_id = f'ai-provider/{item.name}'
                    if item.api_key is not None and item.api_key.get_secret_value():
                        SecretStore().set(secret_id, item.api_key.get_secret_value())
                    row = {
                        'enabled': item.enabled,
                        'base_url': item.base_url,
                        'default_model': item.model,
                        'api_key_env': item.api_key_env,
                        'secret_id': secret_id,
                    }
                    if item.name == 'opencode':
                        row.pop('api_key_env', None)
                        row.pop('secret_id', None)
                        data['open_code'] = {**data.get('open_code', {}), **row}
                    elif item.name in ('openai', 'gemini'):
                        data[item.name] = {**data.get(item.name, {}), **row}
                    else:
                        custom.append({**row, 'name': item.name, 'location': item.location})
                data['openai_compatible'] = custom
        try:
            fresh = edit_configuration(path, change)
            apply_preferences(fresh)
            if payload.autostart is not None:
                from nova.setup.autostart import set_autostart
                set_autostart(payload.autostart, target='nova-desktop')
        except (ValueError, OSError, SecretStoreUnavailable) as exc:
            raise HTTPException(400, str(exc)) from exc
        return status()

    @router.post('/prepare', status_code=202)
    def prepare(payload: PrepareInput | None = None):
        return preparation.start(payload.model if payload else None)

    @router.get('/preparation')
    def preparation_status():
        result = preparation.status()
        if result['status'] == 'ready':
            apply_preferences(load_settings(str(path)))
        return result

    @router.get('/catalog')
    def catalog():
        cap = capability_profile(detect_machine())
        try:
            installed = {m.name for m in state.provider.list_models()}
        except NOVAProviderError:
            installed = set()
        return [dict(name=s.name, role=kind, size_gb=s.weights_gb,
                     compatible=evaluate(s, cap)[0] is not Availability.NOT_RECOMMENDED,
                     installed=s.name in installed or s.name + ':latest' in installed)
                for kind, specs in SPECS_BY_ROLE.items() if kind not in ('fast', 'embedding') for s in specs]

    @router.get('/library')
    def library():
        return state.attachments.list()

    @router.post('/library', status_code=201)
    def upload(payload: AttachmentInput):
        try:
            return state.attachments.add(payload.name, payload.data)
        except (ValueError, OSError, UnicodeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            from nova.core.logging import get_logger
            get_logger('attachments').exception('Could not parse attachment')
            raise HTTPException(400, 'No hemos podido leer este archivo. Prueba con una copia en otro formato.') from exc

    @router.delete('/library/{key}')
    def delete_attachment(key: str):
        try:
            state.attachments.delete(key)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {'deleted': key}

    @router.get('/memory')
    def memory():
        from nova.memory import MemoryStore
        store = MemoryStore(state.settings.memory.db_file)
        try:
            return [dict(id=m.id, content=m.content, created_at=m.created_at) for m in store.list_memories(limit=100)]
        finally:
            store.close()

    @router.delete('/memory/{key}')
    def delete_memory(key: int):
        from nova.memory import MemoryStore
        store = MemoryStore(state.settings.memory.db_file)
        try:
            if not store.delete_memory(key):
                raise HTTPException(404, 'Recuerdo no encontrado.')
        finally:
            store.close()
        return {'deleted': key}

    @router.get('/permissions')
    def permissions():
        return dict(categories=state.settings.permissions.categories, tools=TOOL_CATEGORIES,
                    pending=state.approvals.pending() if state.approvals else [])

    @router.post('/permissions/{key}')
    def approve(key: str, payload: ApprovalInput):
        if not state.approvals or not state.approvals.resolve(key, payload.decision):
            raise HTTPException(409, 'Esta autorización ha caducado o necesita confirmación para cada acción.')
        return {'resolved': True}

    @router.post('/pause')
    def pause(payload: PauseInput):
        state.paused = payload.paused
        if state.approvals:
            state.approvals.cancel_all()
        if state.scheduler:
            if payload.paused:
                state.scheduler.stop()
            else:
                state.scheduler.start(state.executor.run_task)
        return {'paused': state.paused}

    @router.get('/tunnel')
    def tunnel_status():
        tunnel = state.tunnel
        if not tunnel:
            raise HTTPException(501, 'El acceso remoto no está disponible en esta edición.')
        return dict(active=tunnel.active, url=tunnel.url)

    @router.post('/tunnel')
    def tunnel_toggle(payload: TunnelInput, request: Request):
        if request.url.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise HTTPException(403, 'El acceso remoto solo puede activarse o desactivarse desde este ordenador.')
        tunnel = state.tunnel
        if not tunnel:
            raise HTTPException(501, 'El acceso remoto no está disponible en esta edición.')
        if payload.active:
            try:
                url = tunnel.start()
            except TunnelError as exc:
                raise HTTPException(400, str(exc)) from exc
            return dict(active=True, url=url)
        tunnel.stop()
        return dict(active=False, url=None)

    return router
