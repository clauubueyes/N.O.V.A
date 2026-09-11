from __future__ import annotations

import secrets
from pathlib import Path
from typing import Callable

import yaml

from nova.core.config import NovaSettings
from nova.core.paths import installation_home
from nova.setup.state import StateStore


def edit_configuration(path: Path, change: Callable[[dict], None]) -> NovaSettings:
    """Validate and atomically commit, retaining fields not owned by this editor."""
    store = StateStore()
    with store.lock():
        data = yaml.safe_load(path.read_text(encoding='utf-8')) if path.exists() else {}
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError('La configuración no se puede leer. Abre Diagnóstico para revisarla.')
        change(data)
        settings = NovaSettings.model_validate(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.yaml.tmp')
        temporary.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')
        temporary.replace(path)
        return settings


def initialize(home: Path | None = None) -> Path:
    home = (home or installation_home()).resolve()
    home.mkdir(parents=True, exist_ok=True)
    path = home / 'config.yaml'
    created = not path.exists()

    def defaults(data):
        data.setdefault('desktop', {})
        api = data.setdefault('api', {})
        api['host'] = '127.0.0.1'
        api['cors_origins'] = []
        api.setdefault('token', secrets.token_urlsafe(32))
        if not api['token']:
            api['token'] = secrets.token_urlsafe(32)
        api.setdefault('host_enabled', True)
        data.setdefault('ai', {'mode': 'local', 'privacy': 'local_only'})
        data.setdefault('memory', {'db_file': str(home / 'data' / 'nova.db')})
        data.setdefault('logging', {'file': str(home / 'logs' / 'nova.log')})
        data.setdefault('audit', {'file': str(home / 'logs' / 'audit.nova.jsonl')})
        permissions = data.setdefault('permissions', {})
        permissions.setdefault('autonomy', 'ask')
        permissions.setdefault('allow', ['date_time', 'calculate', 'remember', 'memory_search'])
        permissions.setdefault('categories', {'reading': 'ask', 'writing': 'ask',
                                              'applications': 'ask', 'commands': 'ask',
                                              'system': 'deny', 'remote': 'deny'})
        data.setdefault('host', {'roots': [], 'commands': [], 'apps': {'notepad': 'notepad.exe', 'calculator': 'calc.exe'}})
        data.setdefault('plugins', {'enabled': ['text_tools', 'units']})

    edit_configuration(path, defaults)
    store = StateStore()
    if created:
        store.record_resource(path, 'config')
    for folder in ('data', 'logs', 'cache'):
        target = home / folder
        if not target.exists():
            target.mkdir()
            store.record_resource(target, 'data' if folder != 'cache' else 'cache')
    store.change(lambda state: setattr(state, 'config_path', str(path)))
    return path
