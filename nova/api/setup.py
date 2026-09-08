from __future__ import annotations

"""Web "installer" endpoints (PHASE 13).

Thin REST bridge over `nova.setup.*` so the ChatGPT-style UI can install and
provision N.O.V.A. from the browser: detect the machine, check Ollama, pull the
recommended models (with live SSE progress), write config.yaml safely and toggle
autostart/voice. These routes mutate the host machine, so they are refused when
the API binds outside localhost without an API token.
"""

import json
import queue
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from nova.core.logging import get_logger
from nova.llm.ollama import OllamaProvider
from nova.setup.detect import (
    detect_machine,
    detect_ollama,
    detect_python_packages,
)
from nova.setup.models import default_model_for, missing_models, normalize_model_name, recommended_models

logger = get_logger("api.setup")


def _settings(request: Request):
    return request.app.state.nova.settings


def _guard(request: Request) -> None:
    """Refuse setup mutations when exposed on the network without a token."""
    settings = _settings(request)
    if settings.api.token:
        return  # the existing /v1/* middleware enforces the Bearer token
    if settings.api.host in ("127.0.0.1", "localhost", "::1"):
        return
    raise HTTPException(
        status_code=403,
        detail="setup routes need api.token when the API binds outside localhost",
    )


def ollama_payload() -> dict[str, Any]:
    st = detect_ollama()
    return {
        "installed": st.installed,
        "running": st.running,
        "models": st.models,
        "message": st.message,
        "started_now": st.started_now,
    }


def config_payload() -> dict[str, Any]:
    from nova.core.paths import default_config_path

    path = default_config_path()
    data: dict[str, Any] = {"path": str(path), "exists": path.exists(), "complete": False}
    if data["exists"]:
        try:
            import yaml

            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            data["sections"] = sorted(cfg.keys())
            data["complete"] = {"llm", "permissions", "host", "voice", "plugins"} <= set(cfg.keys())
        except Exception as exc:  # noqa: BLE001
            data["complete"] = False
            data["error"] = str(exc)
    return data


class ProvisionRequest(BaseModel):
    voice: bool = False
    web: bool = False
    plugins: bool = True
    automation: bool = False
    autostart: bool | None = None
    config_path: str | None = None


class AutostartRequest(BaseModel):
    enable: bool


def build_setup_router() -> APIRouter:
    router = APIRouter(prefix="/v1/setup", tags=["setup"])

    @router.get("/status")
    def setup_status(request: Request) -> dict[str, Any]:
        _guard(request)
        from nova.setup.selector import capability_profile, select_stack

        profile = detect_machine()
        st = detect_ollama()
        recs = recommended_models(profile.ram_total_gb, profile.gpu_vram_gb)
        missing = sorted(missing_models(st.models, profile.ram_total_gb, profile.gpu_vram_gb))
        default_rec = default_model_for(profile.ram_total_gb, profile.gpu_vram_gb)
        cap = capability_profile(profile)
        stack = select_stack(profile, check_disk=True)
        try:
            from nova.setup.autostart import autostart_status

            autostart = autostart_status()
        except Exception as exc:  # noqa: BLE001
            autostart = None
            logger.debug("autostart status unavailable: %s", exc)
        return {
            "machine": {
                "os": profile.os_name,
                "python": profile.python,
                "arch": profile.arch,
                "cpu_count": profile.cpu_count,
                "cpu_model": profile.cpu_model,
                "ram_gb": round(profile.ram_total_gb, 1),
                "ram_available_gb": round(profile.ram_available_gb, 1),
                "gpu_vram_gb": profile.gpu_vram_gb,
                "gpu_available": profile.gpu_available,
                "gpu_vendor": profile.gpu_vendor,
                "gpu_model": profile.gpu_model,
                "gpu_accel": profile.gpu_accel,
                "disk_free_gb": round(profile.disk_free_gb, 1),
            },
            "capability": {"tier": cap.tier, "reasons": cap.reasons},
            "stack": [
                {
                    "role": choice.role,
                    "kind": choice.kind,
                    "model": choice.spec.name,
                    "state": choice.state.value,
                    "install": choice.install,
                    "memory_gb": round(choice.memory_gb, 1),
                }
                for choice in stack
            ],
            "ollama": ollama_payload(),
            "recommended_models": [
                {"role": rec.role, "model": rec.model, "reason": rec.reason} for rec in recs
            ],
            "missing_models": missing,
            "default_model": default_rec.model,
            "config": config_payload(),
            "extras": detect_python_packages(),
            "autostart": autostart,
        }

    @router.post("/provision")
    def provision(request: Request, payload: ProvisionRequest | None = None) -> dict[str, Any]:
        _guard(request)
        from nova.setup.autostart import AutostartError, set_autostart
        from nova.setup.provision import autoconfigure
        from nova.core.paths import default_config_path

        payload = payload or ProvisionRequest()
        profile = detect_machine()
        report = autoconfigure(
            profile,
            path=payload.config_path or default_config_path(),
            enable_plugins=payload.plugins,
            enable_voice=payload.voice,
            enable_web=payload.web,
            enable_automation=payload.automation,
        )
        autostart_status: bool | None = None
        if payload.autostart is not None:
            try:
                set_autostart(payload.autostart)
                autostart_status = payload.autostart
            except AutostartError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "config_path": report.config_path,
            "created": report.created,
            "changed": report.changed,
            "summary": report.summary,
            "autostart": autostart_status,
            "ollama": ollama_payload(),
        }

    @router.get("/pull")
    def pull_models(request: Request, model: str | None = None) -> StreamingResponse:
        """Pull a model (or all recommended missing ones) with SSE progress.

        `?model=llama3.1:8b` pulls a single model; without `model` it pulls every
        recommended model that is missing on the host. Events:
        `{"status": "start"|downloading-status|"done", "model": ..., "completed":?, "total":?, "ok":?}`.
        """
        _guard(request)
        settings = _settings(request)
        st = detect_ollama()
        if not st.running:
            raise HTTPException(status_code=503, detail="Ollama is not running")

        profile = detect_machine()
        recs = recommended_models(profile.ram_total_gb, profile.gpu_vram_gb)
        missing = missing_models(st.models, profile.ram_total_gb, profile.gpu_vram_gb)
        if model:
            targets = [model] if normalize_model_name(model) not in {normalize_model_name(m) for m in st.models} else []
        else:
            targets = [rec.model for rec in recs if normalize_model_name(rec.model) in missing]

        if not targets:
            return StreamingResponse(
                iter([f"data: {json.dumps({'status': 'done', 'model': None, 'ok': True, 'message': 'already complete'})}\n\n"]),
                media_type="text/event-stream",
            )

        puller = OllamaProvider(settings.llm)

        def generate() -> Any:
            try:
                for target in targets:
                    events: queue.Queue[dict] = queue.Queue()
                    events.put({"status": "start", "model": target})

                    def progress(completed: int, total: int | None, status: str) -> None:
                        events.put({"status": status, "model": target, "completed": completed, "total": total})

                    def run(target_name: str) -> None:
                        try:
                            from nova.setup.state import StateStore

                            store = StateStore()
                            with store.lock():
                                store.load()
                                fresh = detect_ollama(settings.llm.base_url)
                                if normalize_model_name(target_name) in {normalize_model_name(m) for m in fresh.models}:
                                    final = "success"
                                else:
                                    final = puller.pull_model(target_name, progress=progress)
                                    if final == "success":
                                        store.record_model(target_name, "web-setup", settings.llm.base_url, owned=True)
                            events.put({"status": "done", "model": target_name, "ok": final == "success"})
                        except Exception as exc:  # noqa: BLE001
                            events.put({"status": "done", "model": target_name, "ok": False, "error": str(exc)})

                    threading.Thread(target=run, args=(target,), daemon=True).start()
                    while True:
                        item = events.get()
                        yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                        if item.get("status") == "done":
                            break
            finally:
                puller.close()

        return StreamingResponse(generate(), media_type="text/event-stream")

    @router.post("/autostart")
    def autostart(request: Request, payload: AutostartRequest) -> dict[str, Any]:
        _guard(request)
        from nova.setup.autostart import AutostartError, autostart_status, set_autostart

        try:
            set_autostart(payload.enable)
        except AutostartError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"enabled": autostart_status()}

    @router.post("/greeting")
    def greeting(request: Request) -> dict[str, Any]:
        """Best-effort spoken JARVIS greeting through the local TTS (no audio leaves the device)."""
        _guard(request)
        from nova.setup.assistant import _say

        text = "Bienvenido, señor. Todos los sistemas están operativos y listos para trabajar."
        _say(text)
        return {"spoken": True, "text": text}

    return router
