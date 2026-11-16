"""Web search and page reading."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

import httpx
from langchain_core.tools import BaseTool, tool

from ..context import AppContext
from .common import safe

USER_AGENT = "Mozilla/5.0 (compatible; DastyarAgent/0.1)"
MAX_DOWNLOAD_BYTES = 5_000_000


def check_url(url: str) -> str | None:
    """Return an error message when the URL must not be fetched."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "only http(s) URLs are supported"
    try:
        for info in socket.getaddrinfo(parsed.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                return f"refusing to fetch link-local/metadata address {ip}"
    except socket.gaierror as e:
        return f"cannot resolve host: {e}"
    return None


def _format_results(results: list[dict]) -> str:
    if not results:
        return "no results"
    return "\n\n".join(
        f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}".rstrip()
        for i, r in enumerate(results, 1)
    )


def _search_ddgs(query: str, n: int) -> list[dict]:
    from ddgs import DDGS

    rows = DDGS().text(query, max_results=n) or []
    return [
        {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
        for r in rows
    ]


def _search_tavily(query: str, n: int, key_env: str) -> list[dict]:
    key = os.environ.get(key_env)
    if not key:
        raise RuntimeError(f"environment variable {key_env} is not set")
    r = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "max_results": n},
        timeout=30,
    )
    r.raise_for_status()
    return [
        {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
        for x in r.json().get("results", [])
    ]


def _search_searxng(query: str, n: int, base: str) -> list[dict]:
    r = httpx.get(
        base.rstrip("/") + "/search",
        params={"q": query, "format": "json"},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    r.raise_for_status()
    return [
        {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
        for x in r.json().get("results", [])[:n]
    ]


def make_web_tools(ctx: AppContext) -> list[BaseTool]:
    @tool(parse_docstring=True)
    @safe
    def web_search(query: str, max_results: int = 6) -> str:
        """Search the web and return titles, URLs and snippets.

        Use web_fetch afterwards to read a promising page in full.

        Args:
            query: Search query.
            max_results: Number of results (1-10).
        """
        n = max(1, min(int(max_results), 10))
        s = ctx.settings.search
        if s.provider == "tavily":
            rows = _search_tavily(query, n, s.tavily_api_key_env)
        elif s.provider == "searxng":
            if not s.searxng_url:
                return "ERROR: search.searxng_url is not configured"
            rows = _search_searxng(query, n, s.searxng_url)
        else:
            rows = _search_ddgs(query, n)
        return _format_results(rows)

    @tool(parse_docstring=True)
    @safe
    def web_fetch(url: str, max_chars: int = 12000) -> str:
        """Download a web page and return its main text content.

        Args:
            url: http(s) URL to read.
            max_chars: Maximum characters to return (default 12000, max 40000).
        """
        problem = check_url(url)
        if problem:
            return f"ERROR: {problem}"
        limit = max(500, min(int(max_chars), 40_000))
        with httpx.Client(
            follow_redirects=True, timeout=30, headers={"User-Agent": USER_AGENT}
        ) as client, client.stream("GET", url) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            body = b""
            for chunk in resp.iter_bytes():
                body += chunk
                if len(body) > MAX_DOWNLOAD_BYTES:
                    break
            encoding = resp.encoding or "utf-8"
        text = body.decode(encoding, errors="replace")
        if "html" in ctype or text.lstrip().lower().startswith(("<!doctype", "<html")):
            import trafilatura

            extracted = trafilatura.extract(
                text, include_links=True, include_tables=True, favor_recall=True
            )
            text = extracted or "(no readable text found on the page)"
        elif not (ctype.startswith("text/") or "json" in ctype or "xml" in ctype):
            return f"ERROR: unsupported content type {ctype or 'unknown'} ({len(body)} bytes)"
        if len(text) > limit:
            text = text[:limit] + f"\n[... truncated, {len(text) - limit} more chars]"
        return text

    return [web_search, web_fetch]
