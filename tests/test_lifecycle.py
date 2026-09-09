from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path

import httpx
import pytest
import yaml

from nova.setup.catalog import SPECS_BY_ROLE, ModelSpec
from nova.setup.detect import MachineProfile, OpenCodeStatus
from nova.setup.models import normalize_model_name
from nova.setup.ollama_lifecycle import OllamaSnapshot
from nova.setup.remote_catalog import CatalogRefresh, parse_catalog, refresh_catalog
from nova.setup.selector import select_stack
from nova.setup.state import InstallationState, ManagedModel, OllamaOwnership, Resource, StateStore, check_removal_path
from nova.setup.update import build_update_plan, recheck_opencode, run_update, write_stack
from nova.setup.remove import build_removal_plan, run_remove


def machine(ram=32, vram=8, disk=200):
    return MachineProfile(ram_total_gb=ram, cpu_count=8, gpu_vram_gb=vram,
                          gpu_available=vram > 0, os_name="Windows 11", python="3.11",
                          arch="AMD64", disk_free_gb=disk)


def stack(profile):
    return {c.role: c.spec.name for c in select_stack(profile) if c.install}


def installed(current):
    return {normalize_model_name(name): "digest" for name in current.values()}


def test_update_no_new_models_no_download_even_low_disk():
    profile = machine(disk=1)
    current = stack(profile)
    plan = build_update_plan(profile, current, installed(current), SPECS_BY_ROLE, "1.0.0")
    assert not plan.changed
    assert not plan.downloads
    assert not plan.blocked


def test_update_new_better_catalog_model():
    profile = machine()
    current = stack(profile)
    catalog = dict(SPECS_BY_ROLE)
    catalog["general"] = [replace(SPECS_BY_ROLE["general"][0], name="better:8b", priority=0)] + catalog["general"]
    plan = build_update_plan(profile, current, installed(current), catalog, "1.0.0")
    assert plan.stack["local"] == "better:8b"
    assert [s.name for s in plan.downloads] == ["better:8b"]
    assert next(c.status for c in plan.changes if c.role == "local") == "NEW"


def test_new_unreviewed_model_not_blindly_promoted():
    catalog = dict(SPECS_BY_ROLE)
    catalog["general"] = [replace(catalog["general"][0], name="unknown:8b", priority=0, verified=False)] + catalog["general"]
    current = stack(machine())
    plan = build_update_plan(machine(), current, installed(current), catalog, "1.0.0")
    assert not plan.changed


@pytest.mark.parametrize("before,after,status", [(8, 32, "UPGRADE"), (32, 8, "DOWNGRADE/REPLACE")])
def test_hardware_changed(before, after, status):
    old, new = machine(before, 0), machine(after, 0)
    current = stack(old)
    plan = build_update_plan(new, current, installed(current), SPECS_BY_ROLE, "1.0.0", asdict(old))
    assert plan.stack["local"] != current["local"]
    assert next(c.status for c in plan.changes if c.role == "local") == status


def test_recommended_model_already_installed_only_switches_config():
    current = stack(machine(8, 0))
    have = installed(stack(machine()))
    plan = build_update_plan(machine(), current, have, SPECS_BY_ROLE, "1.0.0")
    assert plan.changed and not plan.downloads


@pytest.mark.parametrize("disk", [0, 1, 18])
def test_insufficient_total_storage_blocks_downloads(disk):
    plan = build_update_plan(machine(disk=disk), {}, {}, SPECS_BY_ROLE, "1.0.0")
    assert plan.blocked


def test_catalog_minimum_engine_and_architecture():
    catalog = dict(SPECS_BY_ROLE)
    preferred = replace(catalog["general"][0], name="future:8b", priority=0, min_ollama="9.0.0")
    catalog["general"] = [preferred] + catalog["general"]
    assert build_update_plan(machine(), {}, {}, catalog, "1.0.0").stack["local"] != preferred.name
    assert build_update_plan(machine(), {}, {}, catalog, "9.0.0").stack["local"] == preferred.name
    catalog["general"][0] = replace(preferred, architectures=("arm64",))
    assert build_update_plan(machine(), {}, {}, catalog, "9.0.0").stack["local"] != preferred.name


def test_no_compatible_stack_preserves_configuration():
    plan = build_update_plan(machine(1, 0), {"local": "existing"}, {}, SPECS_BY_ROLE, "1.0.0")
    assert plan.blocked


def test_remote_catalog_refresh_cache_offline(tmp_path):
    spec = replace(SPECS_BY_ROLE["general"][0], name="new:8b", priority=0)
    payload = {"schema_version": 1, "models": [asdict(spec)]}
    cache = tmp_path / "catalog.json"
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as client:
        result = refresh_catalog(cache, "https://example.test/catalog", client)
    assert result.new_models == ["new:8b"]
    assert not result.warnings
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        offline = refresh_catalog(cache, "https://example.test/catalog", client)
    assert any(s.name == "new:8b" for s in offline.catalog["general"])
    assert "Using local catalog" in offline.warnings[0]


@pytest.mark.parametrize("field,value", [("weights_gb", -1), ("weights_gb", float("nan")),
                                         ("name", "../../danger"), ("role", "shell"), ("min_ollama", "bad")])
def test_invalid_remote_catalog_rejected(field, value):
    row = asdict(SPECS_BY_ROLE["general"][0])
    row[field] = value
    with pytest.raises(ValueError):
        parse_catalog({"schema_version": 1, "models": [row]})


def update_environment(tmp_path, monkeypatch, current=None):
    import nova.setup.update as update

    current = current or stack(machine())
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"llm": {"models": current, "default_model": current["local"],
                                            "embedding_model": current["embedding"]},
                                     "permissions": {"autonomy": "off"}}), encoding="utf-8")
    have = installed(current)
    monkeypatch.setattr(update, "detect_machine", lambda: machine())
    monkeypatch.setattr(update, "detect_disk_free_gb", lambda p: 200)
    monkeypatch.setattr(update, "refresh_catalog", lambda *a: CatalogRefresh(SPECS_BY_ROLE))
    monkeypatch.setattr(update.ollama, "latest_version", lambda: "1.0.0")
    monkeypatch.setattr(update.ollama, "snapshot", lambda e: OllamaSnapshot(True, "1.0.0", have.copy()))
    monkeypatch.setattr(update, "_pull_one", lambda *a: pytest.fail("Unexpected pull"))
    return config, have


def test_update_idempotent_no_config_write(tmp_path, monkeypatch, capsys):
    config, _ = update_environment(tmp_path, monkeypatch)
    before = config.read_bytes()
    for _ in range(2):
        assert run_update(str(config), confirm=lambda *a, **k: pytest.fail("Unexpected confirmation")) == 0
    assert config.read_bytes() == before
    assert "already up to date" in capsys.readouterr().out


def test_update_ollama_unavailable(tmp_path, monkeypatch):
    import nova.setup.update as update

    config, _ = update_environment(tmp_path, monkeypatch)
    monkeypatch.setattr(update.ollama, "snapshot", lambda e: OllamaSnapshot())
    assert run_update(str(config)) == 1


def test_update_ollama_independent_confirmation(tmp_path, monkeypatch):
    import nova.setup.update as update

    config, _ = update_environment(tmp_path, monkeypatch)
    monkeypatch.setattr(update.ollama, "latest_version", lambda: "2.0.0")
    calls = []
    monkeypatch.setattr(update.ollama, "update_ollama", calls.append)
    assert run_update(str(config), component="ollama", confirm=lambda *a, **k: False) == 0
    assert not calls
    assert run_update(str(config), component="ollama", confirm=lambda *a, **k: True) == 0
    assert calls == ["2.0.0"]
    assert not StateStore().load().ollama.installed_by_nova


def test_update_download_once_and_record_ownership(tmp_path, monkeypatch):
    import nova.setup.update as update

    config, have = update_environment(tmp_path, monkeypatch, stack(machine(8, 0)))
    pulls = []
    def pull(client, name):
        pulls.append(name)
        have[normalize_model_name(name)] = "downloaded-digest"
        return True
    monkeypatch.setattr(update, "_pull_one", pull)
    assert run_update(str(config), confirm=lambda *a, **k: True) == 0
    first = pulls.copy()
    assert first
    assert run_update(str(config), confirm=lambda *a, **k: True) == 0
    assert pulls == first
    state = StateStore().load()
    assert all(m.installed_by_nova and m.digest == "downloaded-digest" for m in state.models)
    assert yaml.safe_load(config.read_text())["permissions"]["autonomy"] == "off"
    assert "llama3.2:3b" in have


def test_failed_update_keeps_config_and_download_journal(tmp_path, monkeypatch):
    import nova.setup.update as update

    config, have = update_environment(tmp_path, monkeypatch, stack(machine(8, 0)))
    before = config.read_bytes()
    def pull(client, name):
        if name.startswith("qwen"):
            return False
        have[normalize_model_name(name)] = "d"
        return True
    monkeypatch.setattr(update, "_pull_one", pull)
    assert run_update(str(config), confirm=lambda *a, **k: True) == 1
    assert config.read_bytes() == before
    assert StateStore().load().models


def test_config_changed_while_downloading_detected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_bytes(b"llm: {}")
    with pytest.raises(ValueError, match="changed during"):
        write_stack(path, b"different", stack(machine()))
    assert path.read_bytes() == b"llm: {}"


def removal_environment(tmp_path, monkeypatch, *, owned_engine=False):
    import nova.setup.remove as removal

    store = StateStore()
    data = tmp_path / "installation" / "data"
    data.mkdir(parents=True)
    (data / "memory.db").write_text("owned", encoding="utf-8")
    state = InstallationState(resources=[Resource(path=str(data), kind="data")],
                              models=[ManagedModel(name="nova:1b", installed_by_nova=True, digest="ours"),
                                      ManagedModel(name="external:7b", installed_by_nova=False)],
                              ollama=OllamaOwnership(installed_by_nova=owned_engine,
                                                     executable=str(tmp_path / "Ollama" / "ollama.exe"), method="official"))
    store.save(state)
    have = {"nova:1b": "ours"}
    monkeypatch.setattr(removal.ollama, "snapshot", lambda e: OllamaSnapshot(True, "1.0.0", have.copy()))
    monkeypatch.setattr(removal.ollama, "model_directory", lambda: tmp_path / "ollama-models")
    monkeypatch.setattr(removal.ollama, "stop_owned_processes", lambda *a, **k: None)
    deleted = []
    def request(req):
        assert req.method == "DELETE"
        name = json.loads(req.content)["model"]
        deleted.append(name)
        have.pop(name, None)
        return httpx.Response(200)
    real_client = httpx.Client
    monkeypatch.setattr(removal.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(request), **kw))
    return store, data, have, deleted


def test_remove_normal_and_repeated(tmp_path, monkeypatch, capsys):
    store, data, _, deleted = removal_environment(tmp_path, monkeypatch)
    assert run_remove(store=store, confirm=lambda *a, **k: True) == 0
    assert not data.exists()
    assert deleted == ["nova:1b"]
    assert not store.path.exists()
    assert run_remove(store=store) == 0
    assert "Nothing to remove" in capsys.readouterr().out


@pytest.mark.parametrize("answers", [[False], [True, False]])
def test_remove_double_confirmation(tmp_path, monkeypatch, answers):
    store, data, _, deleted = removal_environment(tmp_path, monkeypatch)
    iterator = iter(answers)
    assert run_remove(store=store, confirm=lambda *a, **k: next(iterator)) == 0
    assert data.exists() and not deleted and store.path.exists()


@pytest.mark.parametrize("owned,external,expected", [(True, False, True), (True, True, False), (False, False, False)])
def test_remove_engine_ownership_and_external_models(tmp_path, monkeypatch, owned, external, expected):
    import nova.setup.remove as removal

    store, _, have, deleted = removal_environment(tmp_path, monkeypatch, owned_engine=owned)
    if external:
        have["external:7b"] = "external"
    calls = []
    monkeypatch.setattr(removal.ollama, "uninstall_ollama", lambda *a: calls.append(True))
    assert run_remove(store=store, confirm=lambda *a, **k: True) == 0
    assert bool(calls) is expected
    assert deleted == ["nova:1b"]
    if external:
        assert have == {"external:7b": "external"}


def test_remove_changed_managed_model_is_kept(tmp_path, monkeypatch):
    store, _, have, deleted = removal_environment(tmp_path, monkeypatch)
    have["nova:1b"] = "user-replaced"
    assert run_remove(store=store, confirm=lambda *a, **k: True) == 0
    assert not deleted and have["nova:1b"] == "user-replaced"


def opencode_update_environment(tmp_path, monkeypatch, *, mode="hybrid"):
    import nova.setup.detect as detect

    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"ai": {"mode": mode, "privacy": "cloud_allowed" if mode == "hybrid" else "local_only"},
                                     "open_code": {"enabled": True, "base_url": "http://127.0.0.1:4096",
                                                   "default_model": "", "models": {}},
                                     "permissions": {"autonomy": "off"}}), encoding="utf-8")
    store = StateStore(tmp_path / "state.json")
    installed = False
    status = lambda **kw: OpenCodeStatus(installed=installed, running=kw.get("running", False),
                                         version=kw.get("version", ""), configured=kw.get("configured", False),
                                         base_url="http://127.0.0.1:4096", providers=kw.get("providers", []),
                                         models=kw.get("models", []), message=kw.get("message", ""))
    monkeypatch.setattr(detect, "detect_opencode", lambda *a: status(**{"running": True,
        "version": "1.0.0", "configured": True, "providers": ["openai-login"],
        "models": ["openai-login/gpt-5", "anthropic-login/claude-sonnet-4"],
        "message": "opencode-1.0.0 serving"}))
    return config, store


def test_update_recheck_opencode_hybrid_refreshes_catalog(tmp_path, monkeypatch):
    config, store = opencode_update_environment(tmp_path, monkeypatch)
    recheck_opencode(config, store, confirm=lambda *a, **k: True)
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["open_code"]["models"] == {"gpt-5": "openai-login/gpt-5",
                                           "claude-sonnet-4": "anthropic-login/claude-sonnet-4"}
    assert data["open_code"]["default_model"] == "gpt-5"
    assert store.load().opencode_catalog == data["open_code"]["models"]


def test_update_recheck_opencode_hybrid_without_confirm_keeps_config(tmp_path, monkeypatch):
    config, store = opencode_update_environment(tmp_path, monkeypatch)
    before = config.read_bytes()
    recheck_opencode(config, store, confirm=lambda *a, **k: False)
    assert config.read_bytes() == before
    assert store.load() is None or not store.load().opencode_catalog


def test_update_recheck_opencode_local_mode_never_touches_config(tmp_path, monkeypatch, capsys):
    config, store = opencode_update_environment(tmp_path, monkeypatch, mode="local")
    before = config.read_bytes()
    recheck_opencode(config, store, confirm=lambda *a, **k: True)
    assert config.read_bytes() == before
    assert "LOCAL mode" in capsys.readouterr().out


def test_update_recheck_opencode_not_running_is_advice_only(tmp_path, monkeypatch, capsys):
    import nova.setup.detect as detect

    config, store = opencode_update_environment(tmp_path, monkeypatch)
    monkeypatch.setattr(detect, "detect_opencode", lambda *a: OpenCodeStatus(
        installed=True, running=False, version="1.0.0", configured=True,
        base_url="http://127.0.0.1:4096", providers=[], models=[],
        message="OpenCode binary found but the server is not running"))
    before = config.read_bytes()
    recheck_opencode(config, store, confirm=lambda *a, **k: pytest.fail("No confirmation expected"))
    assert config.read_bytes() == before
    assert "server is not running" in capsys.readouterr().out


def test_remove_offline_preserves_journal_and_resources(tmp_path, monkeypatch):
    import nova.setup.remove as removal

    store, data, _, deleted = removal_environment(tmp_path, monkeypatch)
    monkeypatch.setattr(removal.ollama, "snapshot", lambda e: OllamaSnapshot())
    assert run_remove(store=store, confirm=lambda *a, **k: pytest.fail("Should block before confirmation")) == 1
    assert data.exists() and store.path.exists() and not deleted


def test_removal_plan_notes_opencode_is_never_owned(tmp_path):
    from nova.setup.remove import RemovalPlan, build_removal_plan
    from nova.setup.state import InstallationState

    plan: RemovalPlan = build_removal_plan(InstallationState())
    assert any("OpenCode" in note and "never installed or owned" in note for note in plan.notes)


def test_remove_never_touches_repository(tmp_path, monkeypatch):
    store, data, _, _ = removal_environment(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "config").mkdir()
    (repo / "src").mkdir()
    (repo / "README.md").write_text("documentation")
    (repo / "config" / "config.yaml").write_text("source config")
    (repo / "src" / "app.py").write_text("source code")
    (repo / ".git" / "HEAD").write_text("git metadata")
    before = {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    state = store.load()
    state.repository_roots.append(str(repo))
    for path in [repo, repo / ".git", repo / "config", repo / "src", repo / "README.md", tmp_path]:
        state.resources.append(Resource(path=str(path), kind="data"))
    store.save(state)
    monkeypatch.chdir(repo)
    assert run_remove(store=store, confirm=lambda *a, **k: True) == 0
    after = {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    assert before == after
    assert not data.exists()


def test_remove_external_virtualenv(tmp_path, monkeypatch):
    store, _, _, _ = removal_environment(tmp_path, monkeypatch)
    venv = tmp_path / "owned-venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = fake")
    store.record_resource(venv, "venv")
    assert run_remove(store=store, confirm=lambda *a, **k: True) == 0
    assert not venv.exists()


def test_remove_owned_autostart(tmp_path, monkeypatch):
    from nova.setup import autostart

    store, _, _, _ = removal_environment(tmp_path, monkeypatch)
    identity = {"kind": "registry", "value": "nova-agent"}
    store.change(lambda s: setattr(s, "autostart", identity))
    removed = []
    monkeypatch.setattr(autostart, "remove_owned_autostart", lambda *a: removed.append(a[0]))
    assert run_remove(store=store, confirm=lambda *a, **k: True) == 0
    assert removed == [identity]


def test_changed_autostart_keeps_journal(tmp_path, monkeypatch):
    from nova.setup import autostart

    store, data, _, _ = removal_environment(tmp_path, monkeypatch)
    store.change(lambda s: setattr(s, "autostart", {"kind": "registry", "value": "ours"}))
    monkeypatch.setattr(autostart, "autostart_identity", lambda: {"kind": "registry", "value": "someone else"})
    assert run_remove(store=store, confirm=lambda *a, **k: True) == 1
    assert data.exists() and store.path.exists()


def test_ownership_never_adopts_preexisting_models():
    store = StateStore()
    store.record_model("preexisting:latest", "general", "http://localhost:11434", owned=False)
    store.record_model("preexisting", "general", "http://localhost:11434", owned=True)
    state = store.load()
    assert len(state.models) == 1
    assert not state.models[0].installed_by_nova


def test_corrupt_state_refuses_removal(tmp_path):
    store = StateStore(tmp_path / "broken.json")
    store.path.write_text("{bad state")
    with pytest.raises(ValueError, match="ownership guesses"):
        run_remove(store=store)


def test_symlink_to_repository_protected(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    link = tmp_path / "link"
    try:
        link.symlink_to(repo, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink privilege unavailable")
    with pytest.raises(ValueError):
        check_removal_path(link, [repo])


def test_cleanup_worker_repository_guard(tmp_path):
    from nova.setup.cleanup_worker import safe_target

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    for path in [repo, repo / ".git", tmp_path]:
        with pytest.raises(ValueError):
            safe_target(path, [repo])


def test_cli_compatible_commands():
    from nova.setup.cli import build_parser

    parser = build_parser()
    assert parser.parse_args([]).command is None
    for name in ("install", "auto", "update", "remove", "doctor", "status"):
        assert parser.parse_args([name]).command == name
    assert parser.parse_args(["autostart", "--enable"]).enable == 1
    assert parser.parse_args(["autostart", "--enable", "0"]).enable == 0
    assert parser.parse_args(["autostart", "--disable"]).enable == 0


def test_library_discovers_new_family_and_known_quantization():
    from nova.setup.remote_catalog import discover_library

    def respond(request):
        if request.url.path == "/library":
            html = '<a href="/library/new-family">new</a><a href="/library/llama3.1">known</a>'
        elif request.url.path == "/library/llama3.1/tags":
            html = '<a href="/library/llama3.1:8b-instruct-q5_K_M">5.8GB</a>'
        elif request.url.path == "/library/new-family/tags":
            html = '<a href="/library/new-family:4b">2.5GB</a>'
        else:
            return httpx.Response(404)
        return httpx.Response(200, text=html)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        specs = discover_library(client, SPECS_BY_ROLE["general"])
    known = next(s for s in specs if s.name.startswith("llama3.1"))
    new = next(s for s in specs if s.name.startswith("new-family"))
    assert known.verified and known.priority == 0 and known.memory_gb > 6.9
    assert not new.verified


def test_ollama_latest_version_compatible_release(monkeypatch):
    import nova.setup.ollama_lifecycle as engine
    from types import SimpleNamespace

    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(engine.sys, "getwindowsversion", lambda: SimpleNamespace(build=22631), raising=False)
    monkeypatch.setattr(engine.platform, "machine", lambda: "AMD64")
    release = {"tag_name": "v2.0.0", "assets": [{"name": "OllamaSetup.exe"}]}
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=release))) as client:
        assert engine.latest_version(client) == "2.0.0"
    release["prerelease"] = True
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=release))) as client:
        assert engine.latest_version(client) == ""


def test_ollama_snapshot_exact_endpoint_and_normalization(monkeypatch):
    import nova.setup.ollama_lifecycle as engine

    def respond(request):
        assert request.url.port == 12345
        return httpx.Response(200, json={"models": [{"name": "embedding:latest", "digest": "x"}]}
                              if request.url.path == "/api/tags" else {"version": "1.0.0"})
    real_client = httpx.Client
    monkeypatch.setattr(engine.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    result = engine.snapshot("http://localhost:12345")
    assert result.models == {"embedding": "x"} and result.version == "1.0.0"


def test_uninstaller_uses_registered_directory_and_not_rmtree(tmp_path, monkeypatch):
    import nova.setup.ollama_lifecycle as engine

    directory = tmp_path / "custom Ollama"
    directory.mkdir()
    exe = directory / "ollama.exe"
    exe.touch()
    uninstaller = directory / "unins000.exe"
    uninstaller.touch()
    calls = []
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(engine, "stop_owned_processes", lambda path: calls.append(path))
    monkeypatch.setattr(engine, "run_system", lambda args: calls.append(args))
    ownership = OllamaOwnership(installed_by_nova=True, executable=str(exe), method="official")
    engine.uninstall_ollama(ownership, [])
    assert calls[0] == exe
    assert calls[1][0] == str(uninstaller)
    assert directory.exists()


def test_cleanup_worker_removes_external_environment_only(tmp_path):
    from nova.setup.cleanup_worker import cleanup

    environment = tmp_path / "venv"
    environment.mkdir()
    (environment / "pyvenv.cfg").write_text("owned")
    state = tmp_path / "state.json"
    state.write_text("state")
    cleanup({"roots": [], "state": str(state), "paths": [str(environment)], "state_content": "state"})
    assert not environment.exists() and not state.exists()


def test_cleanup_worker_refuses_changed_installation(tmp_path):
    from nova.setup.cleanup_worker import cleanup

    environment = tmp_path / "venv"
    environment.mkdir()
    state = tmp_path / "state.json"
    state.write_text("new installation")
    with pytest.raises(ValueError, match="changed"):
        cleanup({"roots": [], "state": str(state), "paths": [str(environment)], "state_content": "old"})
    assert environment.exists() and state.exists()


def test_runtime_config_and_generated_data_are_registered(monkeypatch):
    from nova.core.paths import installation_home
    from nova.core.config import load_settings
    from nova.setup.provision import autoconfigure

    config = installation_home() / "config.yaml"
    autoconfigure(machine(), config)
    settings = load_settings()
    state = StateStore().load()
    assert state.config_path == str(config)
    assert Path(settings.memory.db_file).is_relative_to(installation_home())
    assert {r.kind for r in state.resources} >= {"config", "data", "cache"}


def test_explicit_install_config_is_used_outside_repository(tmp_path, monkeypatch):
    from nova.core.config import load_settings
    from nova.setup.provision import autoconfigure

    config = tmp_path / "custom.yaml"
    config.write_text("llm:\n  default_model: user-selected\n", encoding="utf-8")
    autoconfigure(machine(), config)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert load_settings().llm.default_model == "user-selected"


def test_cli_install_alias_and_explicit_update_config(monkeypatch, tmp_path):
    import nova.setup.cli as cli
    import nova.setup.update as update

    calls = []
    monkeypatch.setattr(cli, "run_assistant", lambda **kw: calls.append(kw))
    assert cli.main(["install", "--config", str(tmp_path / "custom.yaml")]) == 0
    assert calls[0]["config_path"] == str(tmp_path / "custom.yaml")
    monkeypatch.setattr(update, "run_update", lambda config, **kw: calls.append(config) or 0)
    assert cli.main(["update", "--config=" + str(tmp_path / "custom.yaml")]) == 0
    assert calls[-1] == str(tmp_path / "custom.yaml")
