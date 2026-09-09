from __future__ import annotations

"""PHASE 14.2 — tests for the dependency model, detection and safe install."""

import subprocess

from nova.setup.dependencies import (
    Dependency,
    _first_version,
    audit_dependencies,
    install_ollama,
    install_python,
    summary_of,
    version_key,
)


def _fake_bin_dep(name: str = "fakebin", min_version: str | None = None) -> Dependency:
    return Dependency(
        name=name,
        purpose="test binary",
        binary=name,
        version_args=["--version"],
        version_parser=_first_version,
        min_version=min_version,
        required=False,
    )


def test_catalog_covers_real_runtime() -> None:
    audit = audit_dependencies(features=("core",))
    names = {dep.name for dep in audit.dependencies}
    for required in (
        "python", "venv", "pip", "httpx", "pydantic", "pydantic-settings",
        "PyYAML", "fastapi", "uvicorn", "ollama",
    ):
        assert required in names


def test_core_audit_has_no_missing_required() -> None:
    audit = audit_dependencies(features=("core",))
    assert audit.missing_required == []
    assert audit.ready is True
    assert summary_of(audit) == "all dependencies satisfied"


def test_pyyaml_maps_module_yaml_to_dist_pyyaml() -> None:
    dep = next(dep for dep in audit_dependencies().dependencies if dep.name == "PyYAML")
    assert dep.python_module == "yaml"
    assert dep.dist_name == "PyYAML"
    assert dep.effective_status in ("installed", "outdated")


def test_version_key_comparison() -> None:
    assert version_key("3.11.9") >= version_key("3.11")
    assert not (version_key("3.10.0") >= version_key("3.11"))
    assert version_key("6.0.1") >= version_key("6.0")
    assert version_key("v1.2.3") == (1, 2, 3)


def test_dependency_missing_binary_is_missing(monkeypatch) -> None:
    from nova.setup import dependencies as D

    dep = _fake_bin_dep("definitely-not-a-real-bin-x")
    monkeypatch.setattr(D, "find_binary", lambda name: None)
    assert dep.detect().effective_status == "missing"


def test_dependency_installed_parses_version(monkeypatch) -> None:
    from nova.setup import dependencies as D

    dep = _fake_bin_dep()
    monkeypatch.setattr(D, "find_binary", lambda name: "C:/fake/fakebin.exe")
    monkeypatch.setattr(D, "_run", lambda args, **kw: "fakebin version 1.2.3\n")
    dep.detect()
    assert dep.effective_status == "installed"
    assert dep.version == "1.2.3"


def test_optional_missing_does_not_block_ready(monkeypatch) -> None:
    dep = Dependency(
        name="unused-optional", purpose="", python_module="some-missing-module", required=False
    )
    audit = audit_dependencies(features=("core",), dependencies=[dep])
    assert dep in audit.missing_optional
    assert audit.missing_required == []
    assert audit.ready is True


def test_outdated_classification(monkeypatch) -> None:
    from nova.setup import dependencies as D

    dep = _fake_bin_dep(min_version="2.0")
    monkeypatch.setattr(D, "find_binary", lambda name: "fake")
    monkeypatch.setattr(D, "_run", lambda *a, **k: "fakebin version 1.0.0")
    audit = audit_dependencies(features=("core",), dependencies=[dep])
    assert dep in audit.outdated


def test_install_python_reports_success_and_failure(monkeypatch) -> None:
    dep = Dependency(
        name="xtest", purpose="", python_module="xtest", dist_name="xtest",
        required=True, install_hint="pip install 'xtest'",
    )

    class Ok:
        returncode = 0

    class Bad:
        returncode = 1

    outcomes = iter([Ok(), Bad()])
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: next(outcomes))
    monkeypatch.setattr(dep, "detect", lambda: dep)
    dep.status = "installed"
    assert install_python(dep) is True
    dep.status = "missing"
    assert install_python(dep) is False


def test_install_python_uses_dist_name(monkeypatch) -> None:
    calls: list[list[str]] = []
    dep = Dependency(
        name="PyYAML", purpose="", python_module="yaml", dist_name="PyYAML",
        required=True, install_hint="pip install 'PyYAML'",
    )

    class Ok:
        returncode = 0

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return Ok()

    import nova.setup.dependencies as D

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(dep, "detect", lambda: dep)
    dep.status = "installed"
    assert install_python(dep) is True
    assert "PyYAML" in calls[0]


def test_install_ollama_non_windows_is_false(monkeypatch) -> None:
    import nova.setup.dependencies as D

    monkeypatch.setattr(D.sys, "platform", "linux")
    assert install_ollama() is False


def test_venv_detection_is_consistent() -> None:
    from nova.setup.dependencies import in_venv, venv_is_broken

    # We run under the same interpreter as the venv used by pytest.
    assert isinstance(in_venv(), bool)
    assert venv_is_broken() is False