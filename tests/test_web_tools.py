from __future__ import annotations

import json

import httpx
import pytest

from nova.core.audit import AuditLog
from nova.core.config import PermissionSettings, WebSettings
from nova.tools.permissions import PermissionSystem
from nova.tools.registry import create_registry
from nova.tools.runner import ToolRunner
from nova.tools.web import WebTools, all_web_tools
from nova.tools.web.client import (
    _robots_allowed_path,
    extract_html,
    parse_search_results,
    validate_web_url,
)
from nova.tools.base import ToolError


def _runner(tools, *, autonomy="off", allow=(), tmp_path) -> ToolRunner:
    permissions = PermissionSystem(
        PermissionSettings(autonomy=autonomy, allow=list(allow), deny=[])
    )
    return ToolRunner(
        registry=create_registry(tools),
        permissions=permissions,
        audit=AuditLog(str(tmp_path / "audit.jsonl")),
        confirm=lambda _question: False,
    )


def _settings(**overrides) -> WebSettings:
    settings = WebSettings()
    for key, value in {
        "enabled": True,
        "min_delay_s": 0.0,
        "search_url": "https://html.duckduckgo.com/html/?q={query}",
        **overrides,
    }.items():
        setattr(settings, key, value)
    return settings


SEQ_HTML = """\
<!doctype html>
<html>
<head><title>Example</title></head>
<body>
  <div class="result">
    <a class="result__a" href="//html.duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs">Example Docs</a>
    <a class="result__snippet">The docs are great.</a>
  </div>
  <div class="result">
    <a class="result__a" href="https://openai.com">OpenAI</a>
    <a class="result__snippet">AI research.</a>
  </div>
</body>
</html>
"""

PAGE_HTML = """\
<html>
<head><title>My Page</title><script>alert('x')</script></head>
<body>
<h1>Hello World</h1>
<p>This is   the first paragraph.</p>
<p>The second <b>paragraph</b>.</p>
<a href="/about">About</a>
<a href="mailto:hi@example.com">Mail</a>
<script>var x = 1;</script>
<ul><li>Item one</li><li>Item two</li></ul>
</body>
</html>
"""


def _mock_client(routes: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            text = routes.get("robots", "")
            return httpx.Response(200, text=text)
        for key, (status, body) in routes.get("pages", {}).items():
            if path.startswith(key):
                return httpx.Response(status, text=body)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestAllWebTools:
    def test_disabled_registers_nothing(self) -> None:
        assert all_web_tools(_settings(enabled=False)) == []

    def test_enabled_registers_three(self) -> None:
        names = {tool.name for tool in WebTools(_settings()).all()}
        assert {"web_search", "web_fetch", "web_extract"} == names


class TestWebSearch:
    def test_search_parses_results(self, tmp_path) -> None:
        tools = WebTools(_settings(), client=_mock_client({"pages": {"/html/": (200, SEQ_HTML)}}))
        runner = _runner(tools.all(), allow=["web_search"], tmp_path=tmp_path)
        result = runner.run("web_search", {"query": "nova", "max_results": 5})
        assert result.ok
        results = result.data["results"]
        assert results[0]["url"] == "https://example.com/docs"
        assert results[0]["title"] == "Example Docs"
        assert results[0]["snippet"] == "The docs are great."
        assert results[1]["url"] == "https://openai.com"

    def test_search_respects_max_results(self, tmp_path) -> None:
        tools = WebTools(_settings(), client=_mock_client({"pages": {"/html/": (200, SEQ_HTML)}}))
        runner = _runner(tools.all(), allow=["web_search"], tmp_path=tmp_path)
        result = runner.run("web_search", {"query": "nova", "max_results": 1})
        assert len(result.data["results"]) == 1

    def test_permission_denied_by_default(self, tmp_path) -> None:
        tools = WebTools(_settings(), client=_mock_client({"pages": {"/html/": (200, SEQ_HTML)}}))
        runner = _runner(tools.all(), tmp_path=tmp_path)
        result = runner.run("web_search", {"query": "nova"})
        assert not result.ok
        assert "permission denied" in result.message


class TestWebFetch:
    def test_fetch_extracts_readable_text(self, tmp_path) -> None:
        tools = WebTools(_settings(max_chars=500), client=_mock_client({"pages": {"/p": (200, PAGE_HTML)}}))
        runner = _runner(tools.all(), allow=["web_fetch"], tmp_path=tmp_path)
        result = runner.run("web_fetch", {"url": "https://example.com/p"})
        assert result.ok
        data = result.data
        assert data["title"] == "My Page"
        assert "Hello World" in data["text"]
        assert "first paragraph" in data["text"]
        assert "script" not in data["text"].lower()
        assert "Item one" in data["text"]
        assert data["truncated"] is False

    def test_fetch_rejects_non_http_schemes(self, tmp_path) -> None:
        tools = WebTools(_settings(), client=_mock_client({}))
        runner = _runner(tools.all(), allow=["web_fetch"], tmp_path=tmp_path)
        for url in ("file:///C:/secret.txt", "javascript:alert(1)", "data:text/html,x", "ftp://x.com/a"):
            result = runner.run("web_fetch", {"url": url})
            assert not result.ok
            assert "only http/https" in result.message

    def test_fetch_blocks_robots_disallow(self, tmp_path) -> None:
        settings = _settings()
        client = _mock_client({"robots": "User-agent: *\nDisallow: /private/", "pages": {"/private/": (200, "<p>secret</p>")}})
        tools = WebTools(settings, client=client)
        runner = _runner(tools.all(), allow=["web_fetch"], tmp_path=tmp_path)
        result = runner.run("web_fetch", {"url": "https://example.com/private/data"})
        assert not result.ok
        assert "robots.txt" in result.message

    def test_fetch_allowed_outside_disallow(self, tmp_path) -> None:
        client = _mock_client({"robots": "User-agent: *\nDisallow: /private/", "pages": {"/public": (200, "<p>ok</p>")}})
        tools = WebTools(_settings(), client=client)
        runner = _runner(tools.all(), allow=["web_fetch"], tmp_path=tmp_path)
        result = runner.run("web_fetch", {"url": "https://example.com/public"})
        assert result.ok

    def test_fetch_respects_allow_override(self, tmp_path) -> None:
        robots = "User-agent: *\nDisallow: /private/\nAllow: /private/public/"
        client = _mock_client({"robots": robots, "pages": {"/private/public/": (200, "<p>open</p>")}})
        tools = WebTools(_settings(), client=client)
        runner = _runner(tools.all(), allow=["web_fetch"], tmp_path=tmp_path)
        result = runner.run("web_fetch", {"url": "https://example.com/private/public/ok"})
        assert result.ok

    def test_fetch_no_robots_suffix_allows_all(self, tmp_path) -> None:
        client = _mock_client({"robots": "", "pages": {"/x": (200, "<p>fine</p>")}})
        tools = WebTools(_settings(), client=client)
        runner = _runner(tools.all(), allow=["web_fetch"], tmp_path=tmp_path)
        assert runner.run("web_fetch", {"url": "https://example.com/x"}).ok

    def test_fetch_respect_scheme_validation_even_with_allowed(self, tmp_path) -> None:
        tools = WebTools(_settings(), client=_mock_client({}))
        runner = _runner(tools.all(), allow=["web_fetch"], tmp_path=tmp_path)
        result = runner.run("web_fetch", {"url": "file:///etc/passwd"})
        assert not result.ok
        assert "only http/https" in result.message
        result = runner.run("web_fetch", {"url": "http://user:pass@example.com/"})
        assert not result.ok


class TestWebExtract:
    def test_extract_lists_links(self, tmp_path) -> None:
        tools = WebTools(_settings(), client=_mock_client({"pages": {"/": (200, PAGE_HTML)}}))
        runner = _runner(tools.all(), allow=["web_extract"], tmp_path=tmp_path)
        result = runner.run("web_extract", {"url": "https://example.com/"})
        assert result.ok
        urls = [link["url"] for link in result.data["links"]]
        assert urls == ["https://example.com/about"]


class TestRobotsLogic:
    def test_disallow_path(self) -> None:
        text = "User-Agent: *\nDisallow: /private/"
        assert not _robots_allowed_path(text, "/private/x", "N.O.V.A./1.0")
        assert _robots_allowed_path(text, "/public", "N.O.V.A./1.0")

    def test_targeted_agent(self) -> None:
        text = "User-Agent: *\nDisallow: /all\n\nUser-Agent: nova\nDisallow: /nova-only"
        assert _robots_allowed_path(text, "/nova-only/x", "NOVA/1.0") is False
        assert _robots_allowed_path(text, "/all/x", "NOVA/1.0") is False

    def test_allow_overrides_disallow(self) -> None:
        text = "User-Agent: *\nDisallow: /private/\nAllow: /private/open/"
        assert _robots_allowed_path(text, "/private/open/ok", "N.O.V.A./1.0") is True
        assert _robots_allowed_path(text, "/private/closed", "N.O.V.A./1.0") is False

    def test_empty_disallow_allows_everything(self) -> None:
        text = "User-Agent: *\nDisallow:"
        assert _robots_allowed_path(text, "/anything", "N.O.V.A./1.0") is True


class TestValidation:
    def test_validate_web_url_rejects_bad(self) -> None:
        for url in ("file:///x", "ftp://x", "javascript:x", "data:x", "http://", "http://user:pass@host/"):
            with pytest.raises(ToolError):
                validate_web_url(url)

    def test_validate_web_url_accepts_good(self) -> None:
        assert validate_web_url("https://example.com/path?q=1") == "https://example.com/path?q=1"


class TestHtml:
    def test_extract_links_resolves_relative(self) -> None:
        html = '<html><a href="/a">A</a><a href="https://x.com">X</a><a href="mailto:a@b.c">M</a></html>'
        page = extract_html(html, "https://example.com/base/")
        assert [l["url"] for l in page["links"]] == ["https://example.com/a", "https://x.com"]

    def test_search_parser_skip_empty(self) -> None:
        assert parse_search_results("<html><a class='result__a' href=''></a></html>") == []