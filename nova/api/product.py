from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field

from nova import __version__
from nova.core.config import PermissionSettings, load_settings
from nova.core.paths import default_config_path
from nova.desktop.configuration import edit_configuration
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


class PrepareInput(BaseModel):
    model: str | None = None


class ApprovalInput(BaseModel):
    decision: Literal['once', 'always', 'cancel']


class PauseInput(BaseModel):
    paused: bool


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
        current.permissions.categories = fresh.permissions.categories
        current.host.roots = fresh.host.roots
        state.router = build_router(current.llm, current.model_router, ai_mode=current.ai.mode,
                                    ai_privacy=current.ai.privacy, open_code=current.open_code)
        if state.approvals:
            state.approvals.cancel_all()

    @router.get('/status')
    def status():
        settings = state.settings
        return dict(version=__version__, desktop=desktop, paused=state.paused,
                    onboarding_complete=settings.desktop.onboarding_complete,
                    prepared=settings.desktop.prepared, mode=settings.desktop.mode,
                    model=settings.llm.default_model, privacy=settings.ai.privacy,
                    categories=settings.permissions.categories, roots=settings.host.roots,
                    remote=dict(enabled=False, message='El acceso desde otros dispositivos aún no está activado.'),
                    voice=False)

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
        try:
            fresh = edit_configuration(path, change)
            apply_preferences(fresh)
            if payload.autostart is not None:
                from nova.setup.autostart import set_autostart
                set_autostart(payload.autostart, target='nova-desktop')
        except (ValueError, OSError) as exc:
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
        from dataclasses import asdict
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

    return router
