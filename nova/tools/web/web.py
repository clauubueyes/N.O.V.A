from __future__ import annotations

from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from nova.core.config import WebSettings
from nova.tools.base import BaseTool, ToolResult
from nova.tools.web.client import WebClient, parse_search_results


class WebSearchArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=300, description="Search query")
    max_results: int = Field(default=5, ge=1, le=20, description="Maximum number of results")


class WebSearchTool(BaseTool):
    """Search the web without an API key (default: DuckDuckGo HTML)."""

    name = "web_search"
    description = (
        "Search the web for a query and return ranked results (title, url, snippet). "
        "Use this for up-to-date facts, current events or anything the model can't know."
    )

    def __init__(self, client: WebClient, *, search_url: str) -> None:
        self._client = client
        self._search_url = search_url
        self.input_schema = WebSearchArgs

    def execute(self, params: WebSearchArgs) -> ToolResult:
        url = self._search_url.format(query=quote(params.query))
        body = self._client.fetch(url)
        results = parse_search_results(body.decode("utf-8", errors="replace"))
        return ToolResult.success(
            self.name,
            message=f"{len(results)} results",
            data={"results": results[: params.max_results], "total": len(results)},
        )


class WebFetchArgs(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048, description="http(s) URL to read")
    max_chars: int = Field(default=4000, ge=100, le=20000, description="Max chars to return")


class WebFetchTool(BaseTool):
    """Read a web page and return its readable text (title + body)."""

    name = "web_fetch"
    description = (
        "Fetch an http(s) page and return its readable text (title + main content). "
        "Useful to read the content behind a result from web_search."
    )

    def __init__(self, client: WebClient) -> None:
        self._client = client
        self.input_schema = WebFetchArgs

    def execute(self, params: WebFetchArgs) -> ToolResult:
        page = self._client.fetch_html(params.url)
        text = str(page["text"])
        title = str(page["title"])
        truncated = len(text) > params.max_chars
        text = text[: params.max_chars]
        return ToolResult.success(
            self.name,
            message="ok",
            data={
                "url": params.url,
                "title": title,
                "text": text,
                "truncated": truncated,
            },
        )


class WebExtractArgs(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048, description="http(s) URL to extract from")
    max_links: int = Field(default=20, ge=1, le=100, description="Maximum links to return")


class WebExtractTool(BaseTool):
    """Extract the links (and link text) from a web page."""

    name = "web_extract"
    description = (
        "Extract navigation: visit an http(s) page and list the links in it (text + url). "
        "Useful to explore a site after web_fetch."
    )

    def __init__(self, client: WebClient) -> None:
        self._client = client
        self.input_schema = WebExtractArgs

    def execute(self, params: WebExtractArgs) -> ToolResult:
        page = self._client.fetch_html(params.url)
        links = page["links"]
        return ToolResult.success(
            self.name,
            message=f"{len(links)} links",
            data={
                "url": params.url,
                "title": page["title"],
                "links": links[: params.max_links],
            },
        )


class WebTools:
    """Group of web tools sharing a single WebClient (and therefore a per-host rate limit)."""

    def __init__(self, settings: WebSettings, *, client: httpx.Client | None = None) -> None:
        self.web = WebClient(settings, client=client)
        self.search = WebSearchTool(self.web, search_url=settings.search_url)
        self.fetch = WebFetchTool(self.web)
        self.extract = WebExtractTool(self.web)

    def all(self) -> list[BaseTool]:
        return [self.search, self.fetch, self.extract]

    def close(self) -> None:
        self.web.close()


def all_web_tools(settings: WebSettings) -> list[BaseTool]:
    """Register the web tools only when enabled (off by default; denied by default)."""
    if not settings.enabled:
        return []
    return WebTools(settings).all()