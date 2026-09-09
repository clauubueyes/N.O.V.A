from __future__ import annotations

"""Plan model maintenance separately from downloads and configuration commits."""

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
import yaml

from nova.core.config import load_settings
from nova.core.paths import default_config_path
from nova.setup import ollama_lifecycle as ollama
from nova.setup.assistant import _ask, _pull_one
from nova.setup.catalog import ModelSpec
from nova.setup.detect import MachineProfile, detect_disk_free_gb, detect_machine
from nova.setup.models import normalize_model_name
from nova.setup.remote_catalog import refresh_catalog, version_tuple
from nova.setup.selector import Availability, capability_profile, evaluate, select_stack
from nova.setup.state import InstallationState, StateStore, exclusive


@dataclass
class ModelChange:
    role: str
    current: str
    recommended: str
    status: str
    reason: str


@dataclass
class UpdatePlan:
    changes: list[ModelChange]
    stack: dict[str, str]
    downloads: list[ModelSpec]
    additional_gb: float
    blocked: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.downloads or any(c.current != c.recommended for c in self.changes))


def build_update_plan(profile: MachineProfile, current: dict[str, str], installed: dict[str, str],
                      catalog: dict[str, list[ModelSpec]], version: str,
                      previous_hardware: dict | None = None) -> UpdatePlan:
    cap = capability_profile(profile)
    compatible = {role: [s for s in specs if
                        (not s.min_ollama or bool(version_tuple(version)) and version_tuple(version) >= version_tuple(s.min_ollama))
                        and (not s.architectures or profile.arch.lower() in [a.lower() for a in s.architectures])]
                  for role, specs in catalog.items()}
    selected = {c.role: c for c in select_stack(profile, catalog=compatible) if c.install
                and evaluate(c.spec, cap)[0] is not Availability.NOT_RECOMMENDED}
    proposed = {role: c.spec.name for role, c in selected.items()}
    proposed.update({role: name for role, name in current.items() if role not in
                     ("embedding", "local", "small", "coding", "reasoning", "vision")})
    all_specs = {normalize_model_name(s.name): s for group in catalog.values() for s in group}
    changes = []
    for role in dict.fromkeys([*current, *proposed]):
        old, new = current.get(role, ""), proposed.get(role, "")
        if normalize_model_name(old) == normalize_model_name(new):
            proposed[role] = old
            status, reason = "OK", "Already installed and still recommended"
            if normalize_model_name(new) not in installed:
                status, reason = "NEW", "Configured model is missing locally"
        elif not new:
            status, reason = "DOWNGRADE/REPLACE", "Role no longer suitable for current hardware; model file will be kept"
        else:
            old_spec = all_specs.get(normalize_model_name(old))
            old_bad = old_spec and evaluate(old_spec, cap)[0] is Availability.NOT_RECOMMENDED
            if old_bad:
                status, reason = "DOWNGRADE/REPLACE", "; ".join(evaluate(old_spec, cap)[1])
            elif previous_hardware and any(previous_hardware.get(k) != getattr(profile, k) for k in
                                          ("ram_total_gb", "gpu_vram_gb", "cpu_count", "gpu_model")):
                status, reason = "UPGRADE", "Better match for current hardware"
            else:
                status, reason = "NEW", "Better compatible catalog candidate for this role"
        changes.append(ModelChange(role, old, proposed.get(role, ""), status, reason))
    downloads = {normalize_model_name(c.spec.name): c.spec for c in selected.values()
                 if normalize_model_name(c.spec.name) not in installed}
    total = sum(s.weights_gb for s in downloads.values())
    blocked = []
    if not proposed.get("local") or not proposed.get("embedding"):
        blocked.append("No compatible core stack found; current configuration will be kept")
    if downloads and profile.disk_free_gb <= 0:
        blocked.append("Cannot verify available space on the Ollama model volume")
    elif downloads and total + 12 > profile.disk_free_gb:
        blocked.append(f"Insufficient space: need {total:.1f} GB plus 12 GB reserve; {profile.disk_free_gb:.1f} GB free")
    return UpdatePlan(changes, proposed, list(downloads.values()), total, blocked)


def write_stack(path: Path, expected: bytes | None, stack: dict[str, str]) -> None:
    current = path.read_bytes() if path.exists() else None
    if current != expected:
        raise ValueError("Configuration changed during update; rerun to review a fresh plan")
    data = yaml.safe_load(current) if current else {}
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a YAML mapping")
    llm = data.setdefault("llm", {})
    llm["models"] = stack
    llm["default_model"] = stack["local"]
    llm["embedding_model"] = stack["embedding"]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".nova-tmp")
    temporary.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    temporary.replace(path)


def recheck_opencode(path: Path, store: StateStore, confirm=None) -> None:
    """PHASE 14 — recheck OpenCode (providers/models) without changing anything
    unless the user confirms."""
    confirm = confirm or _ask
    try:
        from nova.setup.detect import detect_opencode

        oc = detect_opencode()

        def _apply(catalog, default_model):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
            oc_conf = data.get("open_code", {}) or {}
            oc_conf["models"] = catalog
            if default_model:
                oc_conf["default_model"] = default_model
            data["open_code"] = oc_conf
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".nova-tmp")
            tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
            tmp.replace(path)
            store.change(lambda s, c=catalog: setattr(s, "opencode_catalog", c))

        print(f"\nOpenCode: {oc.message}")
        if oc.running and oc.providers:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
            mode = (data.get("ai", {}) or {}).get("mode", "local")
            existing_default = ((data.get("open_code", {}) or {}).get("default_model", "") or "").strip()
            catalog = {m.split("/", 1)[1] if "/" in m else m: m for m in oc.models}
            if mode == "hybrid" and oc.models and confirm(
                "OpenCode providers detected. Refresh N.O.V.A.'s cloud model catalog from them?",
                default=False,
            ):
                default_model = existing_default or (oc.models[0].split("/", 1)[1] if "/" in oc.models[0] else oc.models[0])
                _apply(catalog, default_model)
                print("OpenCode catalog refreshed in config.")
            elif mode == "hybrid":
                print("Hybrid mode on - cloud models remain as configured.")
            else:
                print("OpenCode reached but N.O.V.A. is in LOCAL mode (ai.mode=local). No cloud routing.")
        elif oc.installed and not oc.running:
            print("OpenCode is installed but its server is not running; start it and rerun update to import providers.")
        elif not oc.installed:
            print("OpenCode is not installed; hybrid cloud fallback stays disabled until it is.")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] OpenCode recheck skipped: {exc}")


@exclusive
def run_update(config: str | None = None, *, catalog_url: str | None = None,
               component: str | None = None, store: StateStore | None = None,
               confirm=None) -> int:
    confirm = confirm or _ask
    store = store or StateStore()
    state = store.load() or InstallationState()
    path = Path(config or state.config_path or default_config_path())
    expected = path.read_bytes() if path.exists() else None
    settings = load_settings(str(path))
    endpoint = settings.llm.base_url
    if settings.llm.provider != "ollama" or not ollama.local_endpoint(endpoint):
        print("[WARN] Local lifecycle management requires a local Ollama endpoint.")
        return 1
    profile = detect_machine()
    volume = ollama.model_directory()
    while not volume.exists() and volume != volume.parent:
        volume = volume.parent
    profile.disk_free_gb = detect_disk_free_gb(str(volume))
    for line in profile.summary():
        print("  " + line)
    detected = ollama.snapshot(endpoint)
    cache = store.path.parent / "model-catalog.json"
    refreshed = refresh_catalog(cache, catalog_url)
    if cache.exists():
        store.record_resource(cache, "cache")
    for warning in refreshed.warnings:
        print("[WARN] " + warning)
    available = ollama.latest_version()
    ollama_update = bool(version_tuple(detected.version) and version_tuple(available) > version_tuple(detected.version))
    print(f"Ollama: Installed: {detected.version or 'unavailable'}; Available: {available or 'unknown'}")
    if ollama_update:
        print("Ollama update available")
    if not detected.running:
        print(f"[WARN] Ollama not available. Start Ollama and rerun update. {detected.error}")
        return 1
    current = {k: v for k, v in settings.llm.models.items() if v}
    current["local"] = settings.llm.default_model
    current["embedding"] = settings.llm.embedding_model
    plan = build_update_plan(profile, current, detected.models, refreshed.catalog, detected.version, state.hardware)
    print("\nN.O.V.A. update available" if plan.changed else "\nN.O.V.A. is already up to date.")
    print("Current model stack / Recommended:")
    for change in plan.changes:
        print(f"  [{change.status}] {change.role}: {change.current or '(none)'} -> {change.recommended or '(disabled)'}")
        print("      " + change.reason)
    if refreshed.new_models:
        print("New models detected:")
        for name in refreshed.new_models:
            print("  + " + name)
        print("  Unreviewed library discoveries are candidates, pending compatibility metadata.")
    print("Models that remain unchanged: " + ", ".join(c.current for c in plan.changes if c.status == "OK"))
    print(f"Estimated additional storage: {plan.additional_gb:.1f} GB")
    for reason in plan.blocked:
        print("[WARN] " + reason)
    if not plan.changed and not ollama_update:
        return 1 if plan.blocked else 0
    choice = component
    if choice is None and ollama_update:
        print("[1] Update Ollama\n[2] Update models\n[3] Update both\n[4] Cancel")
        choice = {"1": "ollama", "2": "models", "3": "both"}.get(input("Select [4]: ").strip(), "cancel")
    choice = choice or "models"
    if choice == "cancel":
        return 0
    if choice in ("ollama", "both") and ollama_update:
        if confirm(f"Update Ollama {detected.version} -> {available}?", default=False):
            ollama.update_ollama(available)
            store.change(lambda s: setattr(s.ollama, "version", available))
            if choice == "both":
                print("Ollama updated. Re-evaluating models against the running server.")
                return run_update(str(path), catalog_url=catalog_url, component="models", store=store, confirm=confirm)
    if choice == "ollama" or not plan.changed:
        return 0
    if plan.blocked:
        return 1
    overrides = [key for key in os.environ if key in ("NOVA_LLM_DEFAULT_MODEL", "NOVA_LLM_EMBEDDING_MODEL")
                 or key.startswith("NOVA_LLM_MODELS")]
    if overrides:
        print("[WARN] Remove model environment overrides before persisting this stack: " + ", ".join(overrides))
        return 1
    if not confirm("Continue updating models? Old models will be kept.", default=False):
        return 0
    with httpx.Client(base_url=endpoint, timeout=3600) as client:
        for spec in plan.downloads:
            fresh = ollama.snapshot(endpoint)
            if not fresh.running:
                raise OSError("Ollama stopped during update")
            key = normalize_model_name(spec.name)
            if key in fresh.models:
                store.record_model(spec.name, spec.role, endpoint, owned=False)
                continue
            if not _pull_one(client, spec.name):
                print("[WARN] Download failed; current configuration kept. Rerun update to resume.")
                return 1
            store.record_model(spec.name, spec.role, endpoint, owned=True)
            after = ollama.snapshot(endpoint)
            if key not in after.models:
                raise OSError(f"Cannot verify downloaded model: {spec.name}")
            store.record_model(spec.name, spec.role, endpoint, owned=True, digest=after.models[key])
    verified = ollama.snapshot(endpoint)
    if not verified.running or any(normalize_model_name(name) not in verified.models
                                   for role, name in plan.stack.items() if role in
                                   ("local", "embedding", "small", "coding", "reasoning", "vision")):
        raise OSError("Selected models changed during update; current configuration kept")
    write_stack(path, expected, plan.stack)
    if expected is None:
        store.record_resource(path, "config")
    def commit(s):
        s.config_path = str(path.resolve())
        s.stack = plan.stack
        s.hardware = asdict(profile)
    store.change(commit)
    print("N.O.V.A. model stack updated. Previous models were kept.")

    # PHASE 14 — recheck OpenCode (providers/models) without changing anything
    # unless the user confirms.
    recheck_opencode(path, store, confirm)
    return 0
