from __future__ import annotations

import re
import threading
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from nova.core.config import WebSettings
from nova.tools.base import ToolError

_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "li",
        "ul",
        "ol",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "br",
        "tr",
        "table",
        "blockquote",
        "pre",
        "section",
        "article",
        "header",
        "footer",
    }
)
_SKIP_TAGS = frozenset({"script", "style", "template", "noscript", "svg", "head"})


def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class HtmlExtractor(HTMLParser):
    """Turn an HTML document into {title, text, links} for the LLM."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._in_title = False
        self._skip_depth = 0
        self._href: str | None = None
        self._anchor_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS and not self._skip_depth:
            self.text_parts.append("\n")
        if tag == "a":
            attrs_dict = dict(attrs)
            self._href = attrs_dict.get("href") or ""
            self._anchor_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "a" and self._href is not None:
            href = self._href.strip()
            text = _clean_text("".join(self._anchor_parts))
            if href:
                self.links.append((text, href))
            self._href = None
            self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self.text_parts.append(data)
        if self._href is not None:
            self._anchor_parts.append(data)


def extract_html(html: str, base_url: str) -> dict[str, list | str]:
    """Return readable text + links for a downloaded page."""
    parser = HtmlExtractor()
    try:
        parser.feed(html)
    except Exception as exc:  # malformed HTML must not crash the tool
        raise ToolError(f"failed to parse HTML: {exc}") from exc

    links: list[dict[str, str]] = []
    for text, url in parser.links:
        absolute = urljoin(base_url, url)
        if urlparse(absolute).scheme in ("http", "https"):
            links.append({"text": text, "url": absolute})
    return {
        "title": _clean_text(parser.title),
        "text": _clean_text("".join(parser.text_parts)),
        "links": links,
    }


def _real_url(href: str) -> str:
    """Resolve search-engine redirect URLs (e.g. DuckDuckGo `uddg`) to the true href."""
    parsed = urlparse(href)
    url = href
    if parsed.path.startswith("/l/") or "duckduckgo" in (parsed.netloc or ""):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            url = target
    return url


class SearchResultsParser(HTMLParser):
    """Parse DuckDuckGo HTML results into [{title, url, snippet}]."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._in_title_anchor: str | None = None
        self._title_parts: list[str] = []
        self._in_snippet_anchor: str | None = None
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrs_dict = dict(attrs)
        classes = attrs_dict.get("class") or ""
        if "result__a" in classes.split():
            self._in_title_anchor = attrs_dict.get("href") or ""
            self._title_parts = []
        elif "result__snippet" in classes.split():
            self._in_snippet_anchor = True
            self._snippet_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag != "a":
            return
        if self._in_title_anchor is not None:
            self.results.append(
                {
                    "title": _clean_text("".join(self._title_parts)),
                    "url": _real_url(self._in_title_anchor),
                    "snippet": "",
                }
            )
            self._in_title_anchor = None
            self._title_parts = []
        elif self._in_snippet_anchor:
            if self.results:
                self.results[-1]["snippet"] = _clean_text("".join(self._snippet_parts))
            self._in_snippet_anchor = False
            self._snippet_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title_anchor is not None:
            self._title_parts.append(data)
        elif self._in_snippet_anchor:
            self._snippet_parts.append(data)


def parse_search_results(html: str) -> list[dict[str, str]]:
    parser = SearchResultsParser()
    parser.feed(html)
    return [result for result in parser.results if result["url"]]


def _robots_match(path: str, pattern: str) -> bool:
    """RFC 9309-style prefix match: pattern matches path if it is a prefix (after
    trimming the trailing `*` wildcard). Empty pattern matches everything."""
    pattern = pattern.split("*", 1)[0].lstrip("/")
    path = path.lstrip("/")
    return path.startswith(pattern)


def _robots_allowed_path(robots_text: str, path: str, user_agent: str) -> bool:
    """Minimal robots.txt check (RFC 9309 subset: longest Allow/Disallow wins, Allow wins ties)."""
    agent_token = user_agent.split("/", 1)[0].lower()
    groups: list[tuple[list[str], list[str], list[str]]] = []
    agents: list[str] = []
    disallows: list[str] = []
    allows: list[str] = []

    def flush() -> None:
        if agents or disallows or allows:
            groups.append((agents, disallows, allows))

    for raw_line in robots_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if agents or disallows or allows:
                flush()
            agents = [value.lower()]
            disallows, allows = [], []
        elif field == "disallow" and agents:
            disallows.append(value)
        elif field == "allow" and agents:
            allows.append(value)
    flush()

    if not groups:
        return True

    for group_agents, group_disallows, group_allows in groups:
        if "*" in group_agents or agent_token in group_agents:
            best: tuple[int, bool] | None = None  # (match_length, is_disallow)
            for d in group_disallows:
                if not d:  # empty Disallow -> no rule (RFC 9309: no effect)
                    continue
                candidate: tuple[int, bool] | None
                if d == "/":
                    candidate = (0, True)
                elif _robots_match(path, d):
                    candidate = (len(d.split("*", 1)[0]), True)
                else:
                    continue
                if best is None or candidate > best:
                    best = candidate
            for a in group_allows:
                if not a:
                    continue
                candidate = (len(a.split("*", 1)[0]), False) if _robots_match(path, a) else None
                if candidate is not None and (best is None or candidate > best):
                    best = candidate
            if best is not None:
                return not best[1]
    return True


class RobotsChecker:
    def __init__(self, client: httpx.Client, settings: WebSettings) -> None:
        self._client = client
        self._settings = settings
        self._cache: dict[str, str | None] = {}
        self._lock = threading.Lock()

    def allowed(self, host: str, path: str) -> bool:
        if not self._settings.respect_robots:
            return True
        host = host.lower()
        with self._lock:
            robots_text = self._cache.get(host, ...)  # type: ignore[comparison-overlap]
            if robots_text is ...:  # type: ignore[comparison-overlap]
                robots_text = self._fetch_robots(host)
                self._cache[host] = robots_text
        return _robots_allowed_path(robots_text or "", path, self._settings.user_agent)

    def _fetch_robots(self, host: str) -> str | None:
        try:
            response = self._client.get(
                f"https://{host}/robots.txt",
                headers={"User-Agent": self._settings.user_agent},
                timeout=self._settings.timeout_s,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        return response.text[:50_000]


class RateLimiter:
    """Enforce a minimum delay between requests to the same host."""

    def __init__(self, min_delay_s: float) -> None:
        self._min_delay_s = min_delay_s
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        if self._min_delay_s <= 0:
            return
        host = host.lower()
        with self._lock:
            now = time.monotonic()
            last = self._last.get(host)
            if last is not None:
                wait_for = self._min_delay_s - (now - last)
                if wait_for > 0:
                    time.sleep(wait_for)
            self._last[host] = time.monotonic()


def validate_web_url(url: str) -> str:
    """Only http(s) URLs with a host; reject credentials and other schemes."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolError(
            f"unsupported URL scheme {parsed.scheme!r}: only http/https are allowed."
        )
    if not parsed.hostname:
        raise ToolError("URL has no host.")
    if parsed.username or parsed.password:
        raise ToolError("URLs with userinfo (user:pass@) are not allowed.")
    return url


class WebClient:
    """The only way N.O.V.A. touches the network.

    Every request goes through: URL validation -> robots.txt check -> per-host rate
    limit -> size/timeout caps. The LLM never gets a raw network primitive.
    """

    def __init__(self, settings: WebSettings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(
            max_redirects=settings.max_redirects,
            headers={"User-Agent": settings.user_agent},
            timeout=settings.timeout_s,
        )
        self._robots = RobotsChecker(self._client, settings)
        self._ratelimit = RateLimiter(settings.min_delay_s)

    def close(self) -> None:
        self._client.close()

    def _get(self, url: str) -> bytes:
        validate_web_url(url)
        host = urlparse(url).hostname or ""
        if not self._robots.allowed(host, urlparse(url).path or "/"):
            raise ToolError(f"blocked by robots.txt ({urlparse(url).netloc}).")
        self._ratelimit.wait(host)
        try:
            with self._client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise ToolError(f"GET {url} -> HTTP {response.status_code}")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > self._settings.max_bytes:
                        raise ToolError(
                            f"page exceeds size limit ({self._settings.max_bytes} bytes)."
                        )
            return b"".join(chunks)
        except ToolError:
            raise
        except httpx.HTTPError as exc:
            raise ToolError(f"request failed: {exc}") from exc

    def fetch(self, url: str) -> bytes:
        return self._get(url)

    def fetch_html(self, url: str) -> dict[str, str | list]:
        body = self._get(url)
        charset = None
        text = body.decode(charset or "utf-8", errors="replace")
        return extract_html(text, url)