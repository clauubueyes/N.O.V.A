from __future__ import annotations

"""Bounded catalog refresh: reviewed JSON feed or discovery in Ollama's library."""

import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass, field, replace
from html.parser import HTMLParser
from pathlib import Path

import httpx
from pydantic import TypeAdapter

from nova.setup.catalog import ROLE_ORDER, SPECS_BY_ROLE, ModelSpec
from nova.setup.models import normalize_model_name


def version_tuple(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    return tuple(map(int, match.groups())) if match else ()


def get_bytes(client: httpx.Client, url: str, limit: int = 2_000_000) -> bytes:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        chunks = bytearray()
        for chunk in response.iter_bytes():
            chunks.extend(chunk)
            if len(chunks) > limit:
                raise ValueError("Remote catalog exceeds size limit")
        return bytes(chunks)


def parse_catalog(payload: dict) -> list[ModelSpec]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("models"), list):
        raise ValueError("Unsupported model catalog schema")
    if len(payload["models"]) > 2000:
        raise ValueError("Too many catalog entries")
    result = []
    for row in payload["models"]:
        spec = TypeAdapter(ModelSpec).validate_python(row)
        if spec.role not in ROLE_ORDER or not re.fullmatch(r"[\w.-]+(?::[\w.-]+)?", spec.name):
            raise ValueError("Invalid catalog model or role")
        for number in (spec.params_b, spec.weights_gb, spec.context, spec.min_ram_gb,
                       spec.priority, spec.memory_gb if spec.memory_gb is not None else 1):
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0:
                raise ValueError("Invalid catalog estimate")
        if spec.weights_gb <= 0 or spec.context <= 0:
            raise ValueError("Missing catalog estimates")
        if spec.min_ollama and not version_tuple(spec.min_ollama):
            raise ValueError("Invalid minimum Ollama version")
        result.append(spec)
    return result


class LibraryLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: dict[str, str] = {}
        self.current = ""

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            self.current = href.removeprefix("/library/") if href.startswith("/library/") else ""
            if self.current:
                self.links.setdefault(self.current, "")

    def handle_data(self, data):
        if self.current:
            self.links[self.current] += " " + data

    def handle_endtag(self, tag):
        if tag == "a":
            self.current = ""


def discover_library(client: httpx.Client, local: list[ModelSpec]) -> list[ModelSpec]:
    page = LibraryLinks()
    page.feed(get_bytes(client, "https://ollama.com/library?sort=newest").decode())
    families = [name for name in page.links if re.fullmatch(r"[\w.-]+", name)]
    if not families:
        raise ValueError("Ollama library format not recognized")
    known = list(dict.fromkeys(s.name.partition(":")[0] for s in local))
    result = []
    deadline = time.monotonic() + 25
    for family in list(dict.fromkeys(families[:6] + known))[:16]:
        if time.monotonic() > deadline:
            break
        tags = LibraryLinks()
        try:
            tags.feed(get_bytes(client, f"https://ollama.com/library/{family}/tags").decode())
        except httpx.HTTPError:
            continue
        for name, description in tags.links.items():
            if not re.fullmatch(re.escape(family) + r":[\w.-]+", name) or "cloud" in name:
                continue
            size = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB)", description)
            params = re.search(r":(\d+(?:\.\d+)?)[bB](?:-|$)", name)
            if not size or not params:
                continue
            quant = re.search(r"(?:q[4568]_[\w]+|fp16|f16)", name, re.I)
            if "-" in name.partition(":")[2] and not quant:
                continue
            role = "coding" if "coder" in family or "code" in family else "general"
            role = "vision" if "vision" in family or "vl" in family else role
            if "embed" in family:
                role = "embedding"
            weights = float(size[1]) / (1000 if size[2] == "MB" else 1)
            known_variants = [s for s in local if s.verified and s.name.partition(":")[0] == family
                              and re.match(rf"{params[1]}b(?:-|$)", s.name.partition(":")[2], re.I)]
            if known_variants and quant and "-text-" not in name:
                for original in known_variants:
                    if "-" in original.name.partition(":")[2]:
                        continue
                    higher_precision = quant[0].lower().startswith(("q5_", "q6_", "q8_")) and original.quant.upper().startswith("Q4")
                    memory = max(original.mem_estimate(), original.mem_estimate() - original.weights_gb + weights * 1.1)
                    result.append(replace(original, name=name, quant=quant[0], weights_gb=weights * 1.1,
                                          memory_gb=memory, priority=max(0, original.priority - 1) if higher_precision else original.priority + 10,
                                          description="Known model variant; higher precision with a larger resource budget" if higher_precision else "Known model quantization variant"))
                continue
            result.append(ModelSpec(name=name, role=role, params_b=float(params[1]),
                                    quant=quant[0] if quant else "default", context=8192,
                                    weights_gb=weights * 1.1, priority=100,
                                    description="Discovered in Ollama library; compatibility review required",
                                    verified=False))
    if not result:
        raise ValueError("Could not read model variants from Ollama library")
    return result[:1500]


@dataclass
class CatalogRefresh:
    catalog: dict[str, list[ModelSpec]]
    new_models: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def refresh_catalog(cache: Path, url: str | None = None, client: httpx.Client | None = None) -> CatalogRefresh:
    local = {(s.role, normalize_model_name(s.name)): s for specs in SPECS_BY_ROLE.values() for s in specs}
    warnings = []
    if cache.exists():
        try:
            for spec in parse_catalog(json.loads(cache.read_text(encoding="utf-8"))):
                local[(spec.role, normalize_model_name(spec.name))] = spec
        except (OSError, ValueError, TypeError) as exc:
            warnings.append(f"Ignoring invalid catalog cache: {exc}")
    before = set(local)
    owns_client = client is None
    client = client or httpx.Client(timeout=5, follow_redirects=True)
    try:
        source = url or os.environ.get("NOVA_MODEL_CATALOG_URL")
        if source:
            if not source.startswith("https://"):
                raise ValueError("Catalog URL must use HTTPS")
            remote = parse_catalog(json.loads(get_bytes(client, source)))
        else:
            remote = discover_library(client, list(local.values()))
        for spec in remote:
            key = (spec.role, normalize_model_name(spec.name))
            if spec.verified or key not in local:
                local[key] = spec
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schema_version": 1, "models": [asdict(s) for s in local.values()]}), encoding="utf-8")
        temporary.replace(cache)
    except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
        warnings.append(f"Could not refresh model catalog. Using local catalog. ({exc})")
    finally:
        if owns_client:
            client.close()
    grouped = {role: [s for s in local.values() if s.role == role] for role in ROLE_ORDER}
    return CatalogRefresh(grouped, sorted({local[k].name for k in local.keys() - before}), warnings)
