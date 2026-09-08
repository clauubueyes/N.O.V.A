from __future__ import annotations

"""`nova-setup remove`: safe uninstall of the *generated* N.O.V.A. installation.

Removes exactly what the installation state records (see `nova.setup.state`):

  - the N.O.V.A.-managed Ollama models (`ollama rm`), never user-owned models;
  - autostart registration created by the installer;
  - generated/untracked artifacts (.venv, build/dist, logs, memory) when safe;
  - the Ollama application *only* when the installer installed it (never a
    pre-existing Ollama).

The repository itself is never touched: every path is checked with
`safe_to_delete` (git-tracked files and `.git/` are always kept) and afterwards
we verify that every git-tracked file still exists. Idempotent: running it with
no installation reports "nothing to remove".
"""

import subprocess
from pathlib import Path

from nova.setup import state as state_mod
from nova.setup.autostart import autostart_status, set_autostart
from nova.setup.detect import _find_ollama_bin, detect_ollama
from nova.setup.models import normalize_model_name
from nova.setup.state import (
    DEFAULT_ARTIFACTS,
    InstallState,
    clear_state,
    git_tracked_files,
    load_state,
    safe_to_delete,
)

CONFIG_DEFAULT = Path("config/config.yaml")


def _ask(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    raw = input(prompt + suffix).strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "s", "si")


def _remove_managed_models(state: InstallState) -> tuple[int, list[str]]:
    """Delete N.O.V.A.'s own models via `ollama rm`. Returns (failures, removed)."""
    binary = _find_ollama_bin()
    if not binary:
        print("  Ollama binary not found; skipping model removal.")
        return 1, []
    st = detect_ollama()
    if not st.running:
        print(
            "  Ollama is not running; its models were left on disk. "
            "Start Ollama and rerun `nova-setup remove`."
        )
        return 1, []
    installed = {normalize_model_name(m) for m in st.models}
    removed: list[str] = []
    failures = 0
    for model in state.models:
        if normalize_model_name(model.name) not in installed:
            print(f"  [ok] {model.name} already removed")
            continue
        try:
            proc = subprocess.run(
                [binary, "rm", model.name],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"  [err] could not remove {model.name}: {exc}")
            failures += 1
            continue
        if proc.returncode == 0:
            removed.append(model.name)
            print(f"  [ok] removed model {model.name}")
        else:
            print(f"  [err] ollama rm {model.name}: {proc.stderr.strip() or proc.returncode}")
            failures += 1
    return failures, removed


def _uninstall_ollama_app(state: InstallState, yes: bool) -> None:
    """Offer to uninstall Ollama only when the installer installed it."""
    if not state.ollama_installed_by_nova:
        print("  Ollama was installed before N.O.V.A. -> kept (not removed).")
        return
    print(
        "  Ollama was installed by N.O.V.A. Uninstalling it also deletes "
        "ALL models stored in it (including any you pulled yourself)."
    )
    if not (yes or _ask("Uninstall the Ollama application too?", default=False)):
        print("  Ollama application kept.")
        return
    import shutil

    winget = shutil.which("winget")
    if not winget:
        print(
            "  winget not found. Uninstall Ollama manually from "
            "Settings > Apps, then rerun `nova-setup remove`."
        )
        return
    try:
        proc = subprocess.run(
            [winget, "uninstall", "--id", "Ollama.Ollama", "-e",
             "--accept-source-agreements"],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        print(f"  [ok] Ollama uninstalled" if proc.returncode == 0
              else f"  [err] Ollama uninstall: {proc.stderr.strip() or proc.returncode}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  [err] could not run winget: {exc}")


def _remove_artifacts(state: InstallState, root: Path) -> list[str]:
    """Delete generated/untracked files; repo files are always kept."""
    removed: list[str] = []
    candidates: list[str] = list(DEFAULT_ARTIFACTS)
    for extra in state.artifacts:
        if extra not in candidates:
            candidates.append(extra)
    if state.venv and state.venv.strip():
        venv_path = Path(state.venv)
        try:
            rel = str(venv_path.resolve().relative_to(root.resolve()))
            if rel not in candidates:
                candidates.append(rel)
        except ValueError:
            pass  # venv lives outside the install root: not ours to delete

    for candidate in candidates:
        target = (root / candidate) if not Path(candidate).is_absolute() else Path(candidate)
        if not target.exists():
            continue
        if not safe_to_delete(target, root):
            print(f"  [KEEP] {candidate} tracked by git - not touched")
            continue
        import shutil

        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed.append(candidate)
            print(f"  [ok] removed {candidate}")
        except OSError as exc:
            print(f"  [err] could not remove {candidate}: {exc}")
    return removed


def _verify_repo_intact(root: Path) -> int:
    """Every git-tracked file must still exist after removal."""
    tracked = git_tracked_files(root)
    if not tracked:
        print("  (no git repository detected - nothing to verify)")
        return 0
    if not (root / ".git").exists():
        print("  [err] repository directory .git is missing.")
        return 1
    missing = [p for p in tracked if not Path(p).exists()]
    if missing:
        print(f"  [err] {len(missing)} tracked file(s) are missing after removal!")
        for path in missing[:10]:
            print("    - " + path)
        return 1
    print(f"  Repository intact: {len(tracked)} tracked files untouched.")
    return 0


def run_remove(config: str | Path = CONFIG_DEFAULT, yes: bool = False) -> int:
    state = load_state(config)
    root = state_mod.install_root_from_config(config) if state is None else state.root_path

    if state is None:
        print("N.O.V.A. installation not found. Nothing to remove.")
        return 0

    print("N.O.V.A. remove\n" + "=" * 40)
    print(f"  Install root : {state.root}")
    if state.venv:
        print(f"  Virtual env  : {state.venv}")
    print(f"  Managed models: {len(state.models)}")
    for model in state.models:
        print(f"    - {model.name} ({model.role or 'unknown role'})")
    artifacts = list(dict.fromkeys([*DEFAULT_ARTIFACTS, *state.artifacts]))
    if state.venv:
        artifacts.insert(0, "the virtual environment")
    print(f"  Generated artifacts: {len(artifacts)} (untracked only)")
    print(
        "  The repository and its source code will NOT be touched "
        "(git-tracked files and .git are always kept)."
    )

    if not yes and not _ask("Remove this N.O.V.A. installation now?", default=False):
        print("Remove aborted.")
        return 0

    # 1) Autostart the installer registered (best-effort).
    if state.autostart or autostart_status():
        try:
            set_autostart(False)
            print("  [ok] autostart disabled")
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] could not disable autostart: {exc}")

    # 2) Managed models (never user-owned ones). Aborts before touching files if
    #    Ollama is not running, so a rerun can still finish the job.
    failures, _removed = _remove_managed_models(state)
    if failures:
        print("  Remove aborted: some models could not be removed.")
        return 1

    # 3) Optional Ollama app removal (double confirmation, N.O.V.A.-installed only).
    _uninstall_ollama_app(state, yes)

    # 4) Generated artifacts (repo-protected).
    _remove_artifacts(state, root)

    # 5) The manifest itself.
    clear_state(config)
    print("  [ok] installation state removed")

    # 6) Verification: nothing from the repository was deleted or damaged.
    rc = _verify_repo_intact(root)
    if rc:
        print("  Remove finished with warnings (see above).")
    else:
        print("Remove complete.")
    return rc