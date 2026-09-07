from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

from nova.core.config import PluginSettings
from nova.plugins.base import Plugin, PluginInfo
from nova.plugins.builtin.text_tools import TextToolsPlugin
from nova.plugins.builtin.units import UnitsPlugin
from nova.tools.base import BaseTool
from nova.tools.registry import ToolRegistry

logger = logging.getLogger("nova.plugins")

# Built-in plugins are exposed by name; they require no extra installation.
_BUILTIN_PLUGINS: dict[str, type[Plugin]] = {
    "text_tools": TextToolsPlugin,
    "units": UnitsPlugin,
}


class PluginLoadError(Exception):
    """Raised when a plugin cannot be loaded (import or contract violation)."""


def _load_external_plugin(path: Path) -> Plugin:
    try:
        module_name = f"nova_plugin_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise PluginLoadError(f"cannot create import spec for {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - a bad plugin must not break startup
        raise PluginLoadError(f"failed to import plugin {path}: {exc}") from exc

    plugin_obj = getattr(module, "PLUGIN", None) or getattr(module, "plugin", None)
    if not isinstance(plugin_obj, Plugin):
        raise PluginLoadError(
            f"plugin {path} must expose a `PLUGIN` object (a nova.plugins.Plugin instance)"
        )
    return plugin_obj


def _scan_dir(settings: PluginSettings) -> list[Plugin]:
    plugins: list[Plugin] = []
    if not settings.dir:
        return plugins
    plugin_dir = Path(settings.dir).expanduser()
    if not plugin_dir.is_dir():
        logger.warning("plugins.dir %s does not exist; skipping dir scan", plugin_dir)
        return plugins
    for path in sorted(plugin_dir.glob("*_plugin.py")):
        try:
            plugins.append(_load_external_plugin(path))
        except PluginLoadError as exc:
            logger.error(f"{exc}")
    return plugins


def load_plugin_tools(
    settings: PluginSettings,
    *,
    registry: ToolRegistry | None = None,
) -> list[PluginInfo]:
    """Instantiate the configured plugins and register their tools.

    Built-in plugins are enabled by name in `settings.enabled`; a custom plugin
    directory (`settings.dir`, optional) is scanned for `*_plugin.py` modules
    that expose a `PLUGIN` object. Every tool is registered in `registry` (a
    fresh empty one if not given) so it flows through the same ToolRunner +
    PermissionSystem + audit as any other capability. A failing plugin is logged
    and skipped without breaking startup (ADR-018).
    """
    registry = registry or ToolRegistry()

    plugins: list[tuple[str, Plugin]] = []
    for name in settings.enabled or []:
        cls = _BUILTIN_PLUGINS.get(name)
        if cls is None:
            logger.warning("unknown built-in plugin %r ignored (enabled list)", name)
            continue
        plugins.append((name, cls()))
    plugins.extend((plugin.name, plugin) for plugin in _scan_dir(settings))

    infos: dict[str, PluginInfo] = {}
    for name, plugin in plugins:
        infos.setdefault(
            name, PluginInfo(name=plugin.name, description=plugin.description or "", tools=[])
        )
        try:
            tools = plugin.tools()
        except Exception as exc:  # noqa: BLE001
            logger.error("plugin %r failed to provide tools: %s", name, exc)
            continue
        tool_names: list[str] = []
        for tool in tools:
            if not isinstance(tool, BaseTool):
                logger.warning("plugin %r returned a non-tool %r; skipped", name, tool)
                continue
            registry.register(tool)
            tool_names.append(tool.name)
        infos[name] = PluginInfo(
            name=plugin.name,
            description=plugin.description or infos[name].description,
            tools=sorted(tool_names),
        )

    return list(infos.values())
