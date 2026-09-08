from __future__ import annotations

"""Ownership-based, resumable removal with mandatory confirmations."""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from nova.setup import ollama_lifecycle as ollama
from nova.setup.assistant import _ask
from nova.setup.models import normalize_model_name
from nova.setup.state import InstallationState, ManagedModel, Resource, StateStore, check_removal_path, exclusive, repository_roots


@dataclass
class RemovalPlan:
    resources: list[Resource] = field(default_factory=list)
    models: list[ManagedModel] = field(default_factory=list)
    keep: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    remove_ollama: bool = False
    autostart: bool = False


def removal_roots(state: InstallationState) -> list[Path]:
    return list(set(repository_roots()) | {Path(p).resolve() for p in state.repository_roots})


def build_removal_plan(state: InstallationState) -> RemovalPlan:
    plan = RemovalPlan(autostart=bool(state.autostart))
    roots = removal_roots(state)
    for resource in state.resources:
        try:
            check_removal_path(Path(resource.path), roots)
            plan.resources.append(resource)
        except (ValueError, OSError) as exc:
            plan.keep.append(str(exc))
    endpoints = {m.endpoint for m in state.models if m.installed_by_nova}
    snapshots = {endpoint: ollama.snapshot(endpoint) for endpoint in endpoints if ollama.local_endpoint(endpoint)}
    for model in state.models:
        if not model.installed_by_nova:
            plan.keep.append(f"External model: {model.name}")
            continue
        if not ollama.local_endpoint(model.endpoint):
            plan.keep.append(f"Remote Ollama model: {model.name}")
            continue
        detected = snapshots[model.endpoint]
        if not detected.running:
            plan.blocked.append(f"Cannot inspect owned model {model.name}: Ollama unavailable")
            continue
        key = normalize_model_name(model.name)
        if key in detected.models and model.digest and model.digest != detected.models[key]:
            plan.keep.append(f"Model changed outside N.O.V.A.: {model.name}")
            continue
        plan.models.append(model)
    if state.ollama.installed_by_nova:
        endpoint = "http://localhost:11434"
        detected = snapshots.get(endpoint) or ollama.snapshot(endpoint)
        owned = {normalize_model_name(m.name) for m in plan.models if m.endpoint == endpoint}
        external = detected.models.keys() - owned
        if state.ollama.executable and not Path(state.ollama.executable).exists() and not state.models:
            plan.keep.append("Ollama application already absent")
        elif not detected.running:
            plan.blocked.append("Cannot check external models; keeping Ollama until it is reachable")
        elif external:
            plan.keep.append("Ollama has external models: " + ", ".join(sorted(external)))
        elif not state.ollama.executable:
            plan.keep.append("Ollama executable ownership is unknown")
        else:
            try:
                check_removal_path(Path(state.ollama.executable).parent, roots)
                plan.remove_ollama = True
            except (ValueError, OSError) as exc:
                plan.keep.append(str(exc))
    else:
        plan.keep.append("Ollama was already installed before N.O.V.A. or ownership is unknown. It will not be removed automatically.")
    return plan


def remove_resource(resource: Resource, roots: list[Path]) -> None:
    path = check_removal_path(Path(resource.path), roots)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def schedule_environment_removal(resources: list[Resource], store: StateStore, roots: list[Path]) -> None:
    for resource in resources:
        check_removal_path(Path(resource.path), roots)
    base_python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    if any(base_python.is_relative_to(Path(r.path).resolve()) for r in resources):
        raise OSError("Cannot remove the running environment without an external Python interpreter")
    payload = {"state": str(store.path.resolve()), "paths": [r.path for r in resources],
               "roots": [str(r) for r in roots], "state_content": store.path.read_text(encoding="utf-8")}
    worker = Path(__file__).with_name("cleanup_worker.py").resolve()
    subprocess.Popen([str(base_python), str(worker), json.dumps(payload)],
                     cwd=str(base_python.parent), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)


@exclusive
def run_remove(*, store: StateStore | None = None, confirm=None) -> int:
    confirm = confirm or _ask
    store = store or StateStore()
    state = store.load()
    if state is None:
        print("N.O.V.A. installation not found.\nNothing to remove.")
        return 0
    roots = removal_roots(state)
    check_removal_path(store.path, roots)
    plan = build_removal_plan(state)
    print("N.O.V.A. removal\n\nThe following will be removed:")
    for resource in plan.resources:
        print(f"  [REMOVE] {resource.kind}: {resource.path}")
    for model in plan.models:
        print(f"  [REMOVE] model: {model.name} ({model.endpoint})")
    if plan.autostart:
        print("  [REMOVE] N.O.V.A. autostart: " + str(state.autostart))
    if plan.remove_ollama:
        print(f"  [REMOVE] Ollama installed by N.O.V.A.: {state.ollama.executable}")
    print(f"  [REMOVE] installation journal: {store.path}")
    for item in plan.keep:
        print("[KEEP] " + item)
    for item in plan.blocked:
        print("[WARN] " + item)
    print("The repository and source code WILL NOT be touched.")
    if plan.blocked:
        print("Removal paused before changes. Start Ollama and retry.")
        return 1
    if not confirm("Continue?", default=False):
        return 0
    if not confirm("Confirm permanent deletion of the listed models/data and selected components?", default=False):
        return 0
    remove_engine = plan.remove_ollama and confirm("Uninstall the N.O.V.A.-owned Ollama application too?", default=False)
    try:
        if sys.platform == "win32":
            for resource in plan.resources:
                if resource.kind == "venv":
                    ollama.stop_owned_processes(check_removal_path(Path(resource.path), roots), environment=True)
        if plan.autostart:
            from nova.setup.autostart import remove_owned_autostart

            remove_owned_autostart(state.autostart, roots)
            store.change(lambda s: s.autostart.clear())
        for model in plan.models:
            fresh = ollama.snapshot(model.endpoint)
            if not fresh.running:
                raise OSError("Ollama stopped during removal; retry after starting it")
            key = normalize_model_name(model.name)
            if key in fresh.models:
                if model.digest and model.digest != fresh.models[key]:
                    raise OSError(f"Model changed since confirmation: {model.name}")
                check_removal_path(ollama.model_directory(), roots)
                with httpx.Client(base_url=model.endpoint, timeout=60) as client:
                    response = client.request("DELETE", "/api/delete", json={"model": model.name})
                    if response.status_code != 404:
                        response.raise_for_status()
            store.change(lambda s, m=model: setattr(s, "models", [x for x in s.models if x != m]))
        if remove_engine:
            fresh = ollama.snapshot("http://localhost:11434")
            if not fresh.running or fresh.models:
                raise OSError("Cannot verify Ollama is empty after removing managed models; keeping it")
            ollama.uninstall_ollama(state.ollama, roots)
            store.change(lambda s: setattr(s.ollama, "installed_by_nova", False))
        deferred = []
        for resource in sorted(plan.resources, key=lambda r: r.kind == "venv"):
            path = Path(resource.path).resolve()
            if sys.platform == "win32" and (Path(sys.executable).resolve().is_relative_to(path)
                                            or any(Path(r.path).resolve().is_relative_to(path) for r in deferred)):
                deferred.append(resource)
                continue
            remove_resource(resource, roots)
            store.change(lambda s, r=resource: setattr(s, "resources", [x for x in s.resources if x != r]))
        if deferred:
            schedule_environment_removal(deferred, store, roots)
            print("Environment cleanup scheduled after this process exits. The journal remains until cleanup succeeds.")
        else:
            check_removal_path(store.path, roots).unlink(missing_ok=True)
            print("N.O.V.A. installation removed. Repository preserved.")
        return 0
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(f"[WARN] {exc}\nRemoval incomplete; ownership journal kept for retry.")
        return 1
