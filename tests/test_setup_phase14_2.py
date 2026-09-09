from __future__ import annotations

"""PHASE 14.2 — tests for `nova setup repair`, non-interactive/JSON CLI and the
real OpenCode /config/providers shape."""

import urllib.request
from pathlib import Path

import pytest

from nova.setup.detect import (
    OpenCodeStatus,
    _opencode_auth,
    _opencode_modules,
    detect_opencode,
)


# --------------------------------------------------------------------------- #
# OpenCode /config/providers real shape
# --------------------------------------------------------------------------- #
def _urlopen_factory(payload: str):
    class Resp:
        status = 200

        def read(self) -> bytes:
            return payload.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return lambda req, timeout=2.0: Resp()


def test_opencode_modules_real_shape(monkeypatch) -> None:
    payload = (
        '{"providers": [{"id": "opencode", "env": ["OPENCODE_API_KEY"], '
        '"options": {"apiKey": "public"}, "models": {"big-pickle": '
        '{"cost": {"input": 0, "output": 0}}}}, {"id": "public", '
        '"models": {"claude": {}}}], "default": {"opencode": "big-pickle"}}'
    )
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_factory(payload))
    providers, models, configured = _opencode_modules("http://127.0.0.1:4096")
    assert providers == ["opencode", "public"]
    assert "opencode/big-pickle" in models
    assert "public/claude" in models
    assert configured is True


def test_opencode_modules_real_shape_without_credentials(monkeypatch) -> None:
    payload = '{"providers": [{"id": "opencode", "models": {"big-pickle": {}}}], "default": {}}'
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_factory(payload))
    providers, models, configured = _opencode_modules("http://127.0.0.1:4096")
    assert providers == ["opencode"]
    assert models == ["opencode/big-pickle"]
    assert configured is False


def test_opencode_modules_legacy_shape_fallback(monkeypatch) -> None:
    payload = '{"opencode": {"models": [{"id": "m1"}]}}'
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_factory(payload))
    providers, models, configured = _opencode_modules("http://127.0.0.1:4096")
    assert providers == ["opencode"]
    assert models == ["opencode/m1"]
    assert configured is False


def test_opencode_modules_unreachable(monkeypatch) -> None:
    def boom(req, timeout=2.0):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    providers, models, configured = _opencode_modules("http://127.0.0.1:4096")
    assert (providers, models, configured) == ([], [], False)


def test_opencode_auth_env_var(monkeypatch) -> None:
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    assert _opencode_auth(paths=[]) == "not_configured"
    monkeypatch.setenv("OPENCODE_API_KEY", "not-the-credential-value")
    assert _opencode_auth() == "available"


def test_opencode_auth_by_file_existence(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    assert _opencode_auth(paths=[auth]) == "available"


def test_detect_opencode_real_server() -> None:
    """Real E2E: skipped cleanly when the OpenCode server is not reachable."""
    from nova.setup.detect import _url_reachable

    if not _url_reachable("http://127.0.0.1:4096", timeout=2.0):
        pytest.skip("OpenCode server is not running; skipping real E2E check")
    status = detect_opencode("http://127.0.0.1:4096")
    assert isinstance(status, OpenCodeStatus)
    assert status.running is True
    assert "opencode" in status.providers
    assert any(model.startswith("opencode/") for model in status.models)
    assert status.auth in ("available", "not_configured", "unknown")


# --------------------------------------------------------------------------- #
# nova setup repair
# --------------------------------------------------------------------------- #
def _machine():
    from nova.setup.detect import MachineProfile

    return MachineProfile(ram_total_gb=32, cpu_count=8, gpu_vram_gb=8,
                          gpu_available=True, os_name="t", python="3")


def test_repair_all_healthy(tmp_path, monkeypatch) -> None:
    from nova.setup import dependencies as deps
    from nova.setup import models as models_mod
    from nova.setup.dependencies import Audit
    from nova.setup.detect import OllamaStatus
    from nova.setup.repair import run_repair
    from nova.setup.terminal import Terminal

    monkeypatch.setattr(deps, "audit_dependencies", lambda *a, **k: Audit(dependencies=[]))
    monkeypatch.setattr(deps, "venv_is_broken", lambda: False)
    monkeypatch.setattr(deps, "install_dependency", lambda dep, **kw: True)
    monkeypatch.setattr("nova.setup.detect.detect_ollama",
                        lambda *a, **k: OllamaStatus(installed=True, running=True, models=["x"], message="serving"))
    monkeypatch.setattr("nova.setup.detect.detect_machine", lambda: _machine())
    monkeypatch.setattr("nova.setup.detect.detect_opencode", lambda *a, **k: OpenCodeStatus(installed=False, message="skip"))
    monkeypatch.setattr(models_mod, "missing_models", lambda *a, **k: [])

    class Report:
        changed = False
        config_path = str(tmp_path / "config.yaml")
        summary = ""

    monkeypatch.setattr("nova.setup.provision.autoconfigure", lambda *a, **k: Report())
    rc = run_repair(str(tmp_path / "config.yaml"), term=Terminal(interactive=False), interactive=False)
    assert rc == 0


def test_repair_installs_missing_required_python(tmp_path, monkeypatch) -> None:
    from nova.setup import dependencies as deps
    from nova.setup import models as models_mod
    from nova.setup.dependencies import Audit, Dependency
    from nova.setup.detect import OllamaStatus
    from nova.setup.repair import run_repair
    from nova.setup.terminal import Terminal

    installed: list[str] = []
    dep = Dependency(name="xtest", purpose="", python_module="xtest", dist_name="xtest",
                     required=True, install_hint="pip install 'xtest'")
    audit = Audit(dependencies=[dep], missing_required=[dep])
    monkeypatch.setattr(deps, "audit_dependencies", lambda *a, **k: audit)
    monkeypatch.setattr(deps, "venv_is_broken", lambda: False)
    monkeypatch.setattr(deps, "install_dependency", lambda d, **kw: installed.append(d.name) or True)
    monkeypatch.setattr("nova.setup.detect.detect_ollama",
                        lambda *a, **k: OllamaStatus(installed=True, running=True, models=["x"], message="serving"))
    monkeypatch.setattr("nova.setup.detect.detect_machine", lambda: _machine())

    class Report:
        changed = False
        config_path = str(tmp_path / "config.yaml")
        summary = ""

    monkeypatch.setattr("nova.setup.provision.autoconfigure", lambda *a, **k: Report())
    monkeypatch.setattr(models_mod, "missing_models", lambda *a, **k: [])
    rc = run_repair(str(tmp_path / "config.yaml"), term=Terminal(interactive=False), interactive=False)
    assert installed == ["xtest"]
    assert rc == 0


def test_repair_warns_on_broken_venv(tmp_path, monkeypatch) -> None:
    from nova.setup import dependencies as deps
    from nova.setup import models as models_mod
    from nova.setup.dependencies import Audit
    from nova.setup.detect import OllamaStatus
    from nova.setup.repair import run_repair
    from nova.setup.terminal import Terminal

    monkeypatch.setattr(deps, "audit_dependencies", lambda *a, **k: Audit(dependencies=[]))
    monkeypatch.setattr(deps, "venv_is_broken", lambda: True)
    monkeypatch.setattr("nova.setup.detect.detect_ollama",
                        lambda *a, **k: OllamaStatus(installed=True, running=True, models=["x"], message="serving"))
    monkeypatch.setattr("nova.setup.detect.detect_machine", lambda: _machine())

    class Report:
        changed = False
        config_path = str(tmp_path / "config.yaml")
        summary = ""

    monkeypatch.setattr("nova.setup.provision.autoconfigure", lambda *a, **k: Report())
    monkeypatch.setattr(models_mod, "missing_models", lambda *a, **k: [])
    rc = run_repair(str(tmp_path / "config.yaml"), term=Terminal(interactive=False), interactive=False)
    assert rc == 1


def test_cli_repair_non_interactive_dispatches(tmp_path, monkeypatch) -> None:
    import nova.setup.cli as cli
    import nova.setup.repair as repair

    calls: list[tuple] = []
    monkeypatch.setattr(repair, "run_repair", lambda config, **kw: calls.append((config, kw)) or 0)
    rc = cli.main(["repair", "--non-interactive", "--config", str(tmp_path / "c.yaml")])
    assert rc == 0
    assert calls[0][0] == str(tmp_path / "c.yaml")
    assert calls[0][1]["interactive"] is False


# --------------------------------------------------------------------------- #
# non-interactive / JSON CLI
# --------------------------------------------------------------------------- #
def test_cli_auto_json_output(tmp_path, monkeypatch, capsys) -> None:
    import nova.setup.cli as cli
    from nova.setup.detect import MachineProfile, OllamaStatus

    monkeypatch.setattr(cli, "detect_machine", lambda: _machine())
    monkeypatch.setattr(cli, "detect_ollama",
                        lambda *a, **k: OllamaStatus(installed=True, running=True, models=["x"], message="serving"))
    monkeypatch.setattr("nova.setup.assistant._pull_models", lambda profile, **kwargs: [])
    cfg = str(tmp_path / "config.yaml")
    rc = cli.main(["auto", "--json", "--config", cfg])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines, "expected some output"
    assert all(line.startswith("{") for line in lines)


def test_cli_auto_non_interactive_flag(tmp_path, monkeypatch) -> None:
    import nova.setup.cli as cli
    from nova.setup.detect import MachineProfile, OllamaStatus

    monkeypatch.setattr(cli, "detect_machine", lambda: _machine())
    monkeypatch.setattr(cli, "detect_ollama",
                        lambda *a, **k: OllamaStatus(installed=True, running=True, models=["x"], message="serving"))
    monkeypatch.setattr("nova.setup.assistant._pull_models", lambda profile, **kwargs: [])
    cfg = str(tmp_path / "config.yaml")
    rc = cli.main(["auto", "--non-interactive", "--config", cfg, "--no-models"])
    assert rc == 0
    assert Path(cfg).exists()


def test_cli_install_alias_non_interactive(monkeypatch, tmp_path) -> None:
    import nova.setup.cli as cli

    calls: list[dict] = []
    monkeypatch.setattr(cli, "run_assistant", lambda **kw: calls.append(kw))
    rc = cli.main(["install", "--non-interactive", "--config", str(tmp_path / "c.yaml")])
    assert rc == 0
    assert calls[0]["config_path"] == str(tmp_path / "c.yaml")
    assert calls[0]["non_interactive"] is True