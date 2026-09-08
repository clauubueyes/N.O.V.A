from __future__ import annotations

"""`nova-setup update`: re-evaluate the installed N.O.V.A. stack.

Re-detects the hardware, refreshes the model catalog (remote, best-effort),
compares what is recommended now against what is configured/installed, and asks
before pulling anything or touching the config. It never deletes models on its
own: removed-from-the-stack or downgraded models are always left in place (the
user can remove them with `nova-setup remove`).

Ollama's own binary is checked but never auto-updated: the wizard only reports
the situation and lets the user choose whether to visit the download page.
"""

import re
import subprocess
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from nova import __version__
from nova.setup.catalog import ROLE_ORDER, spec_by_name
from nova.setup.detect import _find_ollama_bin, detect_machine, detect_ollama
from nova.setup.models import normalize_model_name
from nova.setup.provision import _load_yaml, _write_yaml
from nova.setup.selector import CONFIG_ROLE, capability_profile, plan_lines, select_stack
from nova.setup.state import InstallState, ManagedModel, ensure_state, save_state

CONFIG_DEFAULT = Path("config/config.yaml")

#: Public URL serving the tag list for a model family (informational only).
_OLLAMA_LIBRARY = "https://ollama.com/library/{family}/tags"
_OLLAMA_RELEASES = "https://github.com/ollama/ollama/releases.atom"


@dataclass
class UpdateAction:
    """One role's proposed end state. Never destructive by itself."""

    kind: str                # semantic kind (catalog ROLE_ORDER)
    role: str                # config key (local/small/coding/...)
    marker: str              # OK / KEEP / NEW / UPGRADE / DOWNGRADE
    current: str | None = None
    proposed: str | None = None
    reason: str = ""
    install: bool = False
    write_config: bool = False


def _ask(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    raw = input(prompt + suffix).strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "s", "si")


def _fetch_url(url: str, timeout: float = 8.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _current_for(role: str, config_models: dict, state: InstallState | None) -> str | None:
    """The configured/installed model for a role, preferring user config."""
    if role in config_models and config_models[role]:
        return str(config_models[role])
    if state is not None:
        managed = state.model_by_role(role)
        if managed is not None:
            return managed.name
    return None


# --------------------------------------------------------------------------
# Remote catalog refresh (best-effort; WARN fallback keeps the local catalog).
# --------------------------------------------------------------------------


def refresh_remote_catalog(
    names: list[str],
    fetch=None,
) -> tuple[list[str], bool]:
    """Check the public Ollama library pages for each family.

    Never blocks or changes behavior on failure: every error becomes a `[WARN]`
    line and the caller keeps the local catalog. `fetch` is injectable for tests.
    """
    fetch = fetch or _fetch_url
    lines: list[str] = []
    ok = True
    seen: set[str] = set()
    for name in names:
        family, _, tag = name.partition(":")
        if not family or family in seen:
            continue
        seen.add(family)
        url = _OLLAMA_LIBRARY.format(family=family)
        try:
            body = fetch(url)
        except Exception as exc:  # noqa: BLE001 - offline must never break update
            ok = False
            lines.append(
                f"  [WARN] {family}: could not refresh its catalog "
                f"({type(exc).__name__}); using the local catalog."
            )
            continue
        needle = f">{tag}</" if tag else family
        if body and needle and needle in body:
            lines.append(f"  [INFO] {family}:{tag} available in the Ollama library")
        else:
            lines.append(
                f"  [INFO] {family}:{tag} not found upstream (exists locally; kept)"
            )
            ok = False
    if not ok:
        lines.append("  [WARN] Could not refresh model catalog. Using local catalog.")
    return lines, ok


# --------------------------------------------------------------------------
# Ollama binary version check (informational; never auto-updates).
# --------------------------------------------------------------------------


def _ollama_local_version() -> str | None:
    binary = _find_ollama_bin()
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (proc.stdout or proc.stderr or "").strip()
    for token in out.split():
        if token.replace(".", "").isdigit() and token.count(".") > 0:
            return token.split("-")[0]
    return None


def _ollama_latest_version(fetch=None) -> str | None:
    fetch = fetch or _fetch_url
    try:
        body = fetch(_OLLAMA_RELEASES)
    except Exception:  # noqa: BLE001
        return None
    for line in body.splitlines():
        if "<title>" in line and "ollama v" in line:
            m = re.search(r"ollama v([0-9]+\.[0-9]+\.[0-9]+)", line)
            if m:
                return m.group(1)
    return None


# --------------------------------------------------------------------------
# Stack comparison (pure).
# --------------------------------------------------------------------------


def _action(
    kind: str, role: str, current: str | None, choice
) -> UpdateAction:
    """Compute one role's UpdateAction without any side effect."""
    if choice is None:
        if current is not None:
            return UpdateAction(
                kind, role, "KEEP", current=current,
                reason="no longer recommended for this hardware (kept; remove it manually)",
            )
        return UpdateAction(kind, role, "OK")
    proposed = choice.spec.name

    if current is None:
        # First time for this role (e.g. an upgrade that adds reasoning).
        marker = "NEW" if choice.install else "OPT"
        return UpdateAction(
            kind, role, marker, current=current, proposed=proposed,
            reason="new recommended model for this role",
            install=choice.install,
            write_config=choice.install,
        )

    if normalize_model_name(current) == normalize_model_name(proposed):
        return UpdateAction(kind, role, "OK", current=current, proposed=proposed)

    if current and spec_by_name(current) is None:
        # The user configured something the catalog does not know: keep theirs.
        return UpdateAction(
            kind, role, "KEEP", current=current, proposed=proposed,
            reason=f"custom model '{current}' configured (kept)",
        )

    prop_spec = choice.spec
    cur_spec = spec_by_name(current)
    if cur_spec is None:
        return UpdateAction(kind, role, "OK", current=current, proposed=proposed)

    if prop_spec.weights_gb > cur_spec.weights_gb + 0.05:
        marker = "UPGRADE"
    else:
        marker = "DOWNGRADE"
    reason = "replaced by a better fit for this hardware"
    if marker == "DOWNGRADE":
        reason = f"replaced by a smaller model ({proposed}) to fit this hardware"
    return UpdateAction(
        kind, role, marker, current=current, proposed=proposed,
        reason=reason,
        install=True,
        write_config=True,
    )


def plan_update(
    profile,
    config_models: dict[str, str],
    state: InstallState | None,
) -> list[UpdateAction]:
    """Diff the recommended stack against what is configured now (no I/O)."""
    default_model = config_models.get("__default__")
    choices = select_stack(profile, check_disk=True)
    by_role = {c.role: c for c in choices}
    actions: list[UpdateAction] = []

    for kind in ROLE_ORDER:
        role = CONFIG_ROLE[kind]
        current = _current_for(role, config_models, state)
        if current is None and role == "local":
            current = default_model
        choice = by_role.get(role)
        action = _action(kind, role, current, choice)
        if action.kind == "OK" and current is None and choice is None:
            continue
        actions.append(action)
    return actions


# --------------------------------------------------------------------------
# Apply step: pull chosen models, then write config + state.
# --------------------------------------------------------------------------


def _apply(
    actions: list[UpdateAction],
    profile,
    installed: set[str],
    state: InstallState,
    config_path: Path,
    fetch=None,
) -> list[str]:
    """Install chosen changes (never deletes anything). Returns pulled names."""
    import httpx

    from nova.setup.assistant import _pull_one
    from nova.setup.selector import fallback_candidate

    pending = [a for a in actions if a.marker in ("NEW", "UPGRADE", "DOWNGRADE") and a.proposed]
    targets = [a for a in pending if normalize_model_name(a.proposed) not in installed]
    pulled: list[str] = []

    if targets:
        try:
            with httpx.Client(base_url="http://localhost:11434", timeout=3600) as client:
                for action in targets:
                    name = action.proposed
                    print(f"\n  Pulling {name} ({action.reason})...")
                    if _pull_one(client, name):
                        pulled.append(name)
                        print(f"  [ok] {name} ready")
                        continue
                    fb = fallback_candidate(action.kind, name)
                    if fb and fb.name != name:
                        print(f"  Trying smaller fallback {fb.name} instead...")
                        if _pull_one(client, fb.name):
                            pulled.append(fb.name)
                            action.proposed = fb.name
                            action.write_config = True
                            print(f"  [ok] {fb.name} ready")
                        else:
                            action.write_config = False
                            print(f"  [err] {fb.name} failed too; keeping current model.")
                    else:
                        action.write_config = False
                        print(f"  [err] {name} failed; keeping current model.")
        except Exception as exc:  # noqa: BLE001
            print(f"  Could not reach Ollama to pull models: {exc}")
            for action in targets:
                action.write_config = False

    # Config: flip the confirmed roles to the (possibly fallback) proposed model.
    data = _load_yaml(config_path)
    llm = data.setdefault("llm", {})
    models = llm.setdefault("models", {}) if isinstance(llm.get("models"), dict) else {}
    llm["models"] = models
    changed = False
    for action in actions:
        if not action.write_config or not action.proposed:
            continue
        if models.get(action.role) != action.proposed:
            models[action.role] = action.proposed
            changed = True
        if action.role == "local":
            cur_def = llm.get("default_model")
            previous = action.current
            if previous and cur_def and normalize_model_name(cur_def) == normalize_model_name(previous):
                llm["default_model"] = action.proposed
                changed = True
    if changed:
        _write_yaml(config_path, data)
        print(f"\nConfig updated: {config_path}")

    # State: merged list of N.O.V.A.-pulled models (old ones stay, removable).
    for action in actions:
        if action.proposed and (action.proposed in pulled or action.marker == "OK"):
            state.add_model(
                ManagedModel(
                    name=action.proposed,
                    role=action.role,
                    reason=action.reason if action.proposed in pulled else "installed",
                )
            )
    for name in pulled:
        spec_name = name
        if not state.has_model(spec_name):
            state.add_model(
                ManagedModel(name=spec_name, role="", reason="installed during update")
            )
    return pulled


# --------------------------------------------------------------------------
# Entry point.
# --------------------------------------------------------------------------


def run_update(
    config: str | Path = CONFIG_DEFAULT,
    yes: bool = False,
    fetch=None,
) -> int:
    config_path = Path(config)
    state = ensure_state(config_path)

    print("N.O.V.A. update\n" + "=" * 40)
    profile = detect_machine()
    for line in profile.summary():
        print("  " + line)
    cap = capability_profile(profile)
    print(f"  Capability profile: {cap.tier}")

    st = detect_ollama()
    print("Ollama:", st.message or ("running" if st.running else "not running"))
    installed = {normalize_model_name(m) for m in st.models}

    # Remote catalog check (informational, WARN fallback kept local).
    names = [c.spec.name for c in select_stack(profile, check_disk=True)]
    names += list(state.managed_model_names())
    refresh_lines, _ = refresh_remote_catalog(names, fetch=fetch)
    for _line in refresh_lines:
        print(_line)

    data = _load_yaml(config_path)
    config_models = dict(data.get("llm", {}).get("models", {}) or {})
    default_model = data.get("llm", {}).get("default_model")
    if default_model and "__default__" not in config_models:
        config_models["__default__"] = default_model

    print("\nRecommended stack for this machine:")
    for line in plan_lines(profile):
        print(line)

    actions = plan_update(profile, config_models, state)
    print("\nPlanned changes vs your configuration:")
    active = [a for a in actions if a.marker in ("NEW", "UPGRADE", "DOWNGRADE")]
    for action in actions:
        row = f"  [{action.marker:<9}] {action.role:<10}"
        if action.marker == "OK":
            row += f"{action.current or action.proposed or ''}"
        elif action.proposed:
            row += f"{action.current or '-'} -> {action.proposed}"
        print(row)
        if action.reason and action.marker != "OK":
            print(f"        ({action.reason})")

    if not active:
        print("\n  N.O.V.A. is up to date.")
    else:
        print("\n  N.O.V.A. never deletes models during an update.")
        if not yes:
            print("  Old models are kept; the config below is what will be used.")
            if not _ask("Apply these changes now?", default=True):
                print("Update aborted. No changes were made.")
                _ollama_update_notice(yes, state, fetch=fetch)
                return 0

    pulled = _apply(active, profile, installed, state, config_path, fetch=fetch)

    state.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state.nova_version = __version__
    save_state(state)
    print(f"\nInstallation state saved: {config_path.parent / 'nova-state.json'}")

    _ollama_update_notice(yes, state, fetch=fetch)
    if pulled:
        print("Pulled:", ", ".join(pulled))
    return 0


def _ollama_update_notice(yes: bool, state: InstallState, fetch=None) -> None:
    """Report the Ollama version situation and offer the download page."""
    local_v = _ollama_local_version()
    latest_v = _ollama_latest_version(fetch=fetch)
    if local_v:
        print(f"  Ollama {local_v} installed")
    else:
        print("  Ollama binary not found (version unknown).")
    if latest_v and latest_v != local_v:
        print(f"  [INFO] newer Ollama available: {latest_v}")
        if not yes and _ask("Open the Ollama download page now?", default=False):
            webbrowser.open("https://ollama.com/download")
        print("  (not updated automatically - rerun `nova-setup update` later)")
    elif local_v:
        print("  Ollama is up to date.")