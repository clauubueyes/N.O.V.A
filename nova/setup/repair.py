from __future__ import annotations

"""PHASE 14.2 — `nova setup repair`: audit and fix a broken installation.

Repair never deletes user data, never re-creates directories it does not own and
never touches credentials. It re-checks dependencies, installs safe missing
packages, restarts Ollama when needed, re-runs safe provisioning defaults and
optionally pulls missing recommended models.
"""

from pathlib import Path


def run_repair(
    config_path: str | Path = "config/config.yaml",
    term=None,
    interactive: bool = True,
) -> int:
    """Re-check and repair the installation. Returns an exit code."""
    from nova.setup.dependencies import (
        audit_dependencies,
        install_dependency,
        venv_is_broken,
    )
    from nova.setup.detect import detect_machine, detect_ollama, detect_opencode
    from nova.setup.models import missing_models
    from nova.setup.terminal import Terminal

    out = term or Terminal(interactive=interactive)

    out.header("N.O.V.A. Repair", "check & fix helper")
    fixed: list[str] = []
    warnings: list[str] = []

    # 1) Dependency audit
    out.section("Dependency Check")
    audit = audit_dependencies()
    rows = []
    for dep in audit.dependencies:
        if dep.effective_status == "skip":
            continue
        state = {
            "installed": "OK",
            "outdated": "outdated",
            "missing": "missing" if dep.required else "optional",
        }.get(dep.effective_status, dep.effective_status)
        version = f"v{dep.version}" if dep.version else "-"
        rows.append([dep.name, state, version])
    out.table(["Dependency", "State", "Version"], rows)

    for dep in audit.missing_required:
        if dep.python_module:
            if install_dependency(dep, auto=True):
                fixed.append(f"installed python package {dep.name}")
                out.ok(f"{dep.name} installed.")
            else:
                warnings.append(f"could not install {dep.name}")
                out.warn(f"{dep.name} could not be installed automatically.")
        elif dep.binary == "ollama":
            if install_dependency(dep, auto=True):
                fixed.append("installed Ollama")
                out.ok("Ollama installed.")
            else:
                warnings.append("Ollama still missing (manual install)")
                out.warn(f"Install Ollama manually: {dep.manual_url}")
        elif dep.required:
            warnings.append(f"{dep.name} requires a manual step")
            out.warn(f"{dep.name}: manual step required ({dep.manual_url or dep.install_hint}).")

    # 2) Virtualenv sanity (repair never re-creates it automatically)
    out.section("Environment")
    if venv_is_broken():
        warnings.append("the active virtualenv looks broken")
        out.warn(
            "The active Python virtualenv looks broken. Recreate it manually "
            "(.venv) and run `nova setup` again. N.O.V.A. never re-creates "
            "environments automatically."
        )
    else:
        out.ok("Python virtualenv looks healthy.")

    # 3) Ollama
    out.section("Ollama")
    st = detect_ollama()
    if st.running:
        out.ok("Ollama serving.")
    elif st.installed:
        from nova.setup.detect import ensure_ollama_running

        out.arrow("Starting Ollama...")
        if ensure_ollama_running().running:
            fixed.append("started Ollama")
            out.ok("Ollama started.")
        else:
            warnings.append("Ollama installed but cannot be started")
            out.warn("Ollama installed but not reachable; start it manually (`ollama serve`).")
    else:
        warnings.append("Ollama missing")
        out.warn("Ollama not installed: https://ollama.com/download")

    # 4) Configuration (safe: provisioning never overwrites existing values)
    out.section("Configuration")
    profile = detect_machine()
    from nova.setup.provision import autoconfigure

    report = autoconfigure(
        profile,
        path=config_path,
        enable_plugins=True,
        enable_voice=False,
        enable_web=False,
        enable_automation=False,
    )
    if report.changed:
        fixed.append("provisioned safe config defaults")
        out.sub(f"Wrote: {report.summary}")
    else:
        out.ok("Config already valid.")

    # 5) Models (only when Ollama is reachable)
    st = detect_ollama()
    if st.running:
        out.section("Models")
        missing = missing_models(st.models, profile.ram_total_gb, profile.gpu_vram_gb)
        if missing:
            if interactive and not out.confirm(
                "Run the model wizard to install missing recommended models?", default=True
            ):
                out.sub("Model install skipped.")
            else:
                from nova.setup.assistant import _pull_models

                pulled = _pull_models(profile, config_path=config_path, term=out)
                if pulled:
                    fixed.append(f"pulled {len(pulled)} model(s)")
                out.sub(f"{len(pulled)} model(s) pulled.")
        else:
            out.ok("All recommended models present.")

    # 6) OpenCode consistency (informational; never auto-installs)
    from nova.core.config import load_settings

    settings = load_settings(str(config_path))
    if settings.ai.mode.value == "hybrid":
        out.section("OpenCode")
        oc = detect_opencode(settings.open_code.base_url)
        out.sub(oc.message)
        if oc.running:
            out.sub(f"Auth: {oc.auth}, providers: {', '.join(oc.providers) or 'none'}")
        else:
            warnings.append("Hybrid mode configured but OpenCode not reachable")
            out.warn("Hybrid configured but OpenCode unreachable; install and run it yourself.")

    out.section("Result")
    if warnings:
        for warning in warnings:
            out.warn(warning)
        out.fail("Repair finished with warnings.")
        return 1
    out.ok("Repair finished; everything looks healthy.")
    out.sub("Fixed: " + (", ".join(fixed) if fixed else "nothing needed repair."))
    return 0


__all__ = ["run_repair"]