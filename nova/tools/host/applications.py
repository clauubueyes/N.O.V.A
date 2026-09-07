from __future__ import annotations

import subprocess
import webbrowser
from urllib.parse import urlparse

from pydantic import BaseModel

from nova.tools.base import BaseTool, ToolError, ToolResult

_ALLOWED_SCHEMES = {"http", "https"}


class OpenAppTool(BaseTool):
    """Launch a configured application by name (never an arbitrary path from the LLM)."""

    name = "open_app"
    description = "Launch a configured application by its configured name. Applications must be listed in the host.apps configuration."

    class Args(BaseModel):
        app: str

    input_schema = Args

    def __init__(self, apps: dict[str, str]) -> None:
        self._apps = dict(apps)

    def execute(self, params: BaseModel) -> ToolResult:
        app_name = params.app.strip()
        executable = self._apps.get(app_name)
        if not executable:
            return ToolResult.failure(
                self.name,
                f"application {app_name!r} is not configured. Add it under host.apps in config/config.yaml.",
            )
        try:
            subprocess.Popen([executable], close_fds=True)
        except OSError as exc:
            raise ToolError(f"could not launch {app_name!r}: {exc}") from exc
        return ToolResult.success(
            self.name,
            message=f"launched {app_name}",
            data={"app": app_name},
        )


class OpenUrlTool(BaseTool):
    """Open a valid http(s) URL in the default browser. Dangerous schemes are rejected."""

    name = "open_url"
    description = "Open a valid http(s) URL in the default browser."

    class Args(BaseModel):
        url: str

    input_schema = Args

    def execute(self, params: BaseModel) -> ToolResult:
        url = params.url.strip()
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
            raise ToolError(
                f"unsupported URL or scheme: {url!r}. Only http:// and https:// URLs are allowed."
            )
        webbrowser.open(url)
        return ToolResult.success(
            self.name,
            message=f"opened {url}",
            data={"url": url},
        )


def all_application_tools(apps: dict[str, str]) -> list[BaseTool]:
    return [OpenAppTool(apps), OpenUrlTool()]