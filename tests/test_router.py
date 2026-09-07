from __future__ import annotations

import pytest

from nova.core.config import ModelRouterSettings
from nova.llm.resources import ResourceManager, SystemResources
from nova.llm.router import ModelRouter


def _router(
    catalog=None,
    *,
    resources=None,
    default="llama3.1:8b",
    embedding="nomic-embed-text",
    min_ram=8.0,
    battery=True,
):
    return ModelRouter(
        settings=ModelRouterSettings(min_ram_gb=min_ram, battery=battery),
        catalog=catalog
        or {
            "small": "llama3.2:1b",
            "local": "llama3.1:8b",
            "coding": "qwen2.5-coder:7b",
            "vision": "llama3.2-vision",
            "embedding": embedding,
        },
        default_model=default,
        embedding_model=embedding,
        resources=resources or ResourceManager(
            ram_reader=lambda: {"total": 16.0, "available": 12.0},
            cpu_reader=lambda: (50.0, 8),
            gpu_reader=lambda: (False, 0.0),
            battery_reader=lambda: (None, True),
        ),
    )


def _low_ram() -> ResourceManager:
    return ResourceManager(
        ram_reader=lambda: {"total": 4.0, "available": 1.5},
        cpu_reader=lambda: (50.0, 4),
        gpu_reader=lambda: (False, 0.0),
        battery_reader=lambda: (None, True),
    )


class TestClassify:
    def test_simple_greeting(self) -> None:
        r = _router()
        assert r.classify("hola, cómo estás?") == "simple"

    def test_english_greeting(self) -> None:
        r = _router()
        assert r.classify("Hi there!") == "simple"

    def test_coding_hint(self) -> None:
        r = _router()
        assert r.classify("refactoriza esta funcion en python") == "coding"

    def test_coding_file(self) -> None:
        r = _router()
        assert r.classify("abre config.py y revisa el modulo") == "coding"

    def test_vision_hint(self) -> None:
        r = _router()
        assert r.classify("mira esta imagen y describela") == "vision"

    def test_heavy_hint(self) -> None:
        r = _router()
        assert r.classify("haz un analisis complejo de estos datos") == "heavy"

    def test_general_fallback(self) -> None:
        r = _router()
        assert r.classify("cuéntame una historia") == "general"


class TestRoute:
    def test_simple_uses_small(self) -> None:
        d = _router().route_for("hola!")
        assert d.role == "small"
        assert d.model == "llama3.2:1b"

    def test_coding_uses_coding(self) -> None:
        d = _router().route_for("escribe una funcion en python")
        assert d.role == "coding"
        assert d.model == "qwen2.5-coder:7b"

    def test_coding_constrained_falls_back(self) -> None:
        d = _router(resources=_low_ram()).route_for("escribe una funcion en python")
        assert d.role == "small"
        assert d.model == "llama3.2:1b"

    def test_heavy_uses_local(self) -> None:
        d = _router().route_for("haz un analisis complejo")
        assert d.role == "local"
        assert d.model == "llama3.1:8b"

    def test_heavy_constrained_falls_back(self) -> None:
        d = _router(resources=_low_ram()).route_for("haz un analisis complejo")
        assert d.role == "small"
        assert d.model == "llama3.2:1b"

    def test_general_uses_local(self) -> None:
        d = _router().route_for("cuéntame una historia")
        assert d.model == "llama3.1:8b"

    def test_general_constrained_uses_default(self) -> None:
        # constrained + no small override strategy on general -> default model
        d = _router(resources=_low_ram()).route_for("cuéntame una historia")
        assert d.model == "llama3.1:8b"

    def test_missing_role_falls_back_to_default(self) -> None:
        r = _router(catalog={"small": ""})
        d = r.route_for("hola!")
        assert d.model == "llama3.1:8b"

    def test_unknown_role_falls_back(self) -> None:
        # normalise default catalog: all roles available
        d = _router().route_for("hey")
        assert d.model == "llama3.2:1b"


class TestCatalog:
    def test_catalog_exposed(self) -> None:
        r = _router()
        assert r.catalog()["small"] == "llama3.2:1b"
        assert r.catalog()["coding"] == "qwen2.5-coder:7b"

    def test_default_model_property(self) -> None:
        assert _router().default_model == "llama3.1:8b"


class TestSystemResources:
    def test_snapshot_defaults(self) -> None:
        res = _router()._resources.snapshot()
        assert isinstance(res, SystemResources)
        assert res.ram_available_gb > 0

    def test_low_ram_available_pct(self) -> None:
        res = _router(resources=_low_ram())._resources.snapshot()
        assert res.ram_available_pct() == pytest.approx(0.375)

    def test_battery_downshift_disabled(self) -> None:
        # battery handling configurable via settings.battery
        r = _router(
            battery=False,
            resources=ResourceManager(
                ram_reader=lambda: {"total": 16.0, "available": 12.0},
                cpu_reader=lambda: (50.0, 8),
                gpu_reader=lambda: (False, 0.0),
                battery_reader=lambda: (15.0, False),
            ),
        )
        d = r.route_for("haz un analisis complejo")
        assert d.role == "local"

    def test_readers_that_raise_degrade_gracefully(self) -> None:
        def boom():
            raise RuntimeError("reader failed")

        res = _router(
            resources=ResourceManager(
                ram_reader=boom,
                cpu_reader=boom,
                gpu_reader=boom,
                battery_reader=boom,
            ),
        )._resources.snapshot()
        assert isinstance(res, SystemResources)
        # neutral defaults when readers fail
        assert res.cpu_percent == 50.0
        assert res.battery_percent is None
        assert res.on_ac_power is True

    def test_gpu_reported_when_available(self) -> None:
        res = _router(
            resources=ResourceManager(
                ram_reader=lambda: {"total": 32.0, "available": 28.0},
                cpu_reader=lambda: (30.0, 16),
                gpu_reader=lambda: (True, 8.0),
                battery_reader=lambda: (None, True),
            ),
        )._resources.snapshot()
        assert res.gpu_available is True
        assert res.gpu_vram_gb == 8.0


class TestOllamaSmoke:
    def test_smoke_routes_against_real_ollama(self) -> None:
        from nova.core.config import LLMSettings
        from nova.llm.ollama import OllamaProvider

        settings = LLMSettings()
        provider = OllamaProvider(settings)
        try:
            if not provider.health():
                pytest.skip("Ollama is not reachable")
            models = {m.name for m in provider.list_models()}
            catalog = {
                "small": next((m for m in models if "1b" in m or "0.6b" in m), None),
                "coding": next((m for m in models if "coder" in m), None),
                "local": settings.default_model,
            }
            catalog = {k: v for k, v in catalog.items() if v}
            router = ModelRouter(
                settings=ModelRouterSettings(),
                catalog=catalog,
                default_model=settings.default_model,
                embedding_model=settings.embedding_model,
            )
            decision = router.route_for("hola")
            assert decision.model
        finally:
            provider.close()
