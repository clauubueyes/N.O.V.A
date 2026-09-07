from __future__ import annotations

import json

from nova.core.audit import AuditLog
from nova.core.config import AutonomyLevel, PermissionSettings, PluginSettings
from nova.plugins import PluginLoadError, load_plugin_tools
from nova.plugins.base import Plugin
from nova.tools.base import BaseTool, ToolResult
from nova.tools.permissions import PermissionSystem
from nova.tools.registry import ToolRegistry
from nova.tools.runner import ToolRunner


def _runner(registry, permissions) -> ToolRunner:
    return ToolRunner(
        registry=registry,
        permissions=permissions,
        audit=AuditLog("logs/_test_plugins_audit.jsonl"),
    )


class TestLoader:
    def test_no_plugins_registered_by_default(self) -> None:
        registry = ToolRegistry()
        infos = load_plugin_tools(PluginSettings(), registry=registry)
        assert infos == []
        assert registry.names() == []

    def test_builtin_plugins_load_and_register_tools(self) -> None:
        registry = ToolRegistry()
        infos = load_plugin_tools(PluginSettings(enabled=["text_tools", "units"]), registry=registry)
        names = {info.name for info in infos}
        assert names == {"text_tools", "units"}
        assert sorted(registry.names()) == [
            "convert_length",
            "convert_temperature",
            "convert_weight",
            "text_base64_decode",
            "text_base64_encode",
            "text_slugify",
            "text_uuid",
        ]

    def test_unknown_builtin_is_ignored(self) -> None:
        registry = ToolRegistry()
        infos = load_plugin_tools(PluginSettings(enabled=["nope"]), registry=registry)
        assert infos == []
        assert registry.names() == []

    def test_plugin_info_reports_tools(self) -> None:
        infos = load_plugin_tools(PluginSettings(enabled=["text_tools"]))
        text = next(info for info in infos if info.name == "text_tools")
        assert text.tools == [
            "text_base64_decode",
            "text_base64_encode",
            "text_slugify",
            "text_uuid",
        ]

    def test_does_not_register_when_disabled(self) -> None:
        registry = ToolRegistry()
        load_plugin_tools(PluginSettings(enabled=[]), registry=registry)
        assert "text_slugify" not in registry.names()


class TestExternalDir:
    def test_loads_external_plugin_from_dir(self, tmp_path) -> None:
        (tmp_path / "hello_plugin.py").write_text(
            "from nova.plugins.base import Plugin\n"
            "from nova.tools.base import BaseTool, ToolResult\n"
            "from pydantic import BaseModel\n"
            "\n"
            "class HelloArgs(BaseModel):\n"
            "    name: str = 'world'\n"
            "\n"
            "class HelloTool(BaseTool):\n"
            "    name = 'hello'\n"
            "    description = 'Say hello.'\n"
            "    input_schema = HelloArgs\n"
            "\n"
            "    def execute(self, params):\n"
            "        return ToolResult.success(self.name, data={'greeting': f'hello {params.name}'})\n"
            "\n"
            "class HelloPlugin(Plugin):\n"
            "    name = 'hello'\n"
            "    description = 'External hello plugin.'\n"
            "\n"
            "    def tools(self):\n"
            "        return [HelloTool()]\n"
            "\n"
            "PLUGIN = HelloPlugin()\n",
            encoding="utf-8",
        )
        registry = ToolRegistry()
        infos = load_plugin_tools(PluginSettings(dir=str(tmp_path)), registry=registry)
        assert [info.name for info in infos] == ["hello"]
        assert infos[0].tools == ["hello"]
        assert registry.names() == ["hello"]

    def test_broken_external_plugin_is_skipped(self, tmp_path) -> None:
        (tmp_path / "broken_plugin.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
        registry = ToolRegistry()
        infos = load_plugin_tools(PluginSettings(dir=str(tmp_path)), registry=registry)
        assert infos == []
        assert registry.names() == []

    def test_module_without_plugin_is_skipped(self, tmp_path) -> None:
        (tmp_path / "empty_plugin.py").write_text("x = 1\n", encoding="utf-8")
        infos = load_plugin_tools(PluginSettings(dir=str(tmp_path)))
        assert infos == []

    def test_missing_dir_is_tolerated(self) -> None:
        assert load_plugin_tools(PluginSettings(dir="Z:/does/not/exist")) == []


class TestToolsBehavior:
    def setup_method(self) -> None:
        self.registry = ToolRegistry()
        load_plugin_tools(PluginSettings(enabled=["text_tools", "units"]), registry=self.registry)
        self.runner = _runner(
            self.registry,
            PermissionSystem(PermissionSettings(autonomy=AutonomyLevel.full)),
        )

    def test_base64_roundtrip(self) -> None:
        encoded = self.runner.run("text_base64_encode", {"text": "hola növa"})
        assert encoded.ok is True
        decoded = self.runner.run("text_base64_decode", {"text": encoded.data["encoded"]})
        assert decoded.ok is True
        assert decoded.data["decoded"] == "hola növa"

    def test_base64_invalid_input_is_error(self) -> None:
        result = self.runner.run("text_base64_decode", {"text": "@@@not-base64@@@"})
        assert result.ok is False
        assert "invalid base64" in result.message

    def test_slugify(self) -> None:
        result = self.runner.run("text_slugify", {"text": "Hola Mundo!"})
        assert result.ok is True
        assert result.data["slug"] == "hola-mundo"

    def test_uuid_shape(self) -> None:
        result = self.runner.run("text_uuid")
        assert result.ok is True
        parts = result.data["uuid"].split("-")
        assert len(parts) == 5 and len(parts[0]) == 8

    def test_convert_length_km_to_mi(self) -> None:
        result = self.runner.run("convert_length", {"value": 1, "from_unit": "km", "to_unit": "mi"})
        assert result.ok is True
        assert abs(result.data["result"] - 0.621371) < 1e-5

    def test_convert_weight_kg_to_lb(self) -> None:
        result = self.runner.run("convert_weight", {"value": 1, "from_unit": "kg", "to_unit": "lb"})
        assert result.ok is True
        assert abs(result.data["result"] - 2.2046) < 1e-3

    def test_convert_temperature_c_to_f(self) -> None:
        result = self.runner.run("convert_temperature", {"value": 100, "from_unit": "c", "to_unit": "f"})
        assert result.ok is True
        assert result.data["result"] == 212.0

    def test_unknown_unit_is_error(self) -> None:
        result = self.runner.run("convert_length", {"value": 1, "from_unit": "parsec", "to_unit": "m"})
        assert result.ok is False
        assert "unknown length unit" in result.message


class TestPluginSecurity:
    def test_plugin_tools_stay_under_permissions(self) -> None:
        registry = ToolRegistry()
        load_plugin_tools(PluginSettings(enabled=["text_tools"]), registry=registry)
        deny_all = _runner(
            registry,
            PermissionSystem(PermissionSettings(autonomy=AutonomyLevel.off)),
        )
        result = deny_all.run("text_slugify", {"text": "x"})
        assert result.ok is False
        assert "denied" in result.message

    def test_plugin_tools_are_audited(self, tmp_path) -> None:
        audit_path = tmp_path / "audit.jsonl"
        registry = ToolRegistry()
        load_plugin_tools(PluginSettings(enabled=["units"]), registry=registry)
        runner = ToolRunner(
            registry=registry,
            permissions=PermissionSystem(PermissionSettings(autonomy=AutonomyLevel.full)),
            audit=AuditLog(str(audit_path)),
        )
        runner.run("convert_length", {"value": 1, "from_unit": "m", "to_unit": "km"})
        lines = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        assert lines[0]["tool"] == "convert_length"
        assert lines[0]["decision"] in ("allow", "error")
        assert lines[0]["ok"] is True

    def test_plugin_cannot_bypass_validation(self) -> None:
        registry = ToolRegistry()
        load_plugin_tools(PluginSettings(enabled=["text_tools"]), registry=registry)
        deny_all = _runner(
            registry,
            PermissionSystem(PermissionSettings(autonomy=AutonomyLevel.full)),
        )
        result = deny_all.run("text_base64_encode")
        assert result.ok is False
        assert "invalid arguments" in result.message