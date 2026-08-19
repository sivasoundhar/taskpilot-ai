"""Web Search tool — Tavily primary (if configured), DuckDuckGo MCP fallback.

Tavily (https://tavily.com) is purpose-built for LLM/agent search —
structured results, real content extraction, no HTML-scraping fragility
— but needs a paid/free-tier API key. DuckDuckGo (`duckduckgo-mcp-server`,
see docs/MCP_NOTES.md) needs no key at all. Rather than pick one,
both are wired up: Tavily is tried first when `TAVILY_API_KEY` is set,
and this falls back to the original DuckDuckGo MCP path automatically —
on a missing key, or any Tavily-side failure — so the project still runs
entirely free out of the box for anyone without a Tavily key.

Uniform interface with the other 2 tools (src/tools/file_system.py,
code_execution.py): `async run(...) -> str`, raising a single
tool-specific error type on failure.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from src.config import get_settings
from src.tools.mcp_client import McpToolError, call_tool

logger = logging.getLogger("taskpilot.tools.web_search")

_SERVER_COMMAND = "uvx"
_SERVER_ARGS = ["duckduckgo-mcp-server"]

# Matches mcp_client.call_tool's signature. Injectable so tests can swap in
# a fake and skip spawning a real subprocess / hitting the network — same
# pattern as planner.py's injectable `llm`.
CallTool = Callable[[str, Sequence[str], str, dict], Awaitable[str]]

# (query, max_results, api_key) -> Tavily's raw response dict. Injectable
# for the same reason as CallTool — tests fake this instead of hitting
# Tavily's real API.
TavilySearchFn = Callable[[str, int, str], Awaitable[dict[str, Any]]]


class WebSearchError(RuntimeError):
    """Raised when the search fails or returns nothing usable."""


async def run(
    query: str,
    max_results: int = 5,
    *,
    _call_tool: CallTool = call_tool,
    _tavily_search: TavilySearchFn | None = None,
) -> str:
    """Search the web. Tavily first if TAVILY_API_KEY is configured,
    falling back to DuckDuckGo on a missing key or any Tavily failure.

    Raises WebSearchError only if the query is empty, or *both* the
    attempted backend(s) fail — a Tavily hiccup alone never surfaces as
    an error to the caller, it just falls through silently to DuckDuckGo.
    """
    if not query.strip():
        raise WebSearchError("Empty search query")

    api_key = get_settings().tavily_api_key
    if api_key:
        try:
            return await _run_tavily(query, max_results, api_key, _tavily_search)
        except Exception as exc:  # noqa: BLE001 - any Tavily failure falls back, doesn't hard-fail
            logger.warning("Tavily search failed for %r, falling back to DuckDuckGo: %s", query, exc)

    return await _run_duckduckgo(query, max_results, _call_tool)


async def _default_tavily_search(query: str, max_results: int, api_key: str) -> dict[str, Any]:
    from tavily import AsyncTavilyClient

    client = AsyncTavilyClient(api_key=api_key)
    return await client.search(query, max_results=max_results)


async def _run_tavily(
    query: str, max_results: int, api_key: str, tavily_search: TavilySearchFn | None
) -> str:
    search_fn = tavily_search or _default_tavily_search
    response = await search_fn(query, max_results, api_key)
    results = response.get("results", [])

    if not results:
        raise WebSearchError(f"Tavily search for {query!r} returned no results")

    lines = [f"Found {len(results)} search results:\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        content = r.get("content", "")
        lines.append(f"{i}. {title}\n   URL: {url}\n   Summary: {content}\n")

    output = "\n".join(lines)
    logger.info("Tavily search for %r returned %d chars of results", query, len(output))
    return output


async def _run_duckduckgo(query: str, max_results: int, _call_tool: CallTool) -> str:
    try:
        output = await _call_tool(
            _SERVER_COMMAND,
            _SERVER_ARGS,
            "search",
            {"query": query, "max_results": max_results},
        )
    except McpToolError as exc:
        raise WebSearchError(f"Web search failed for {query!r}: {exc}") from exc

    if not output.strip():
        raise WebSearchError(f"Web search for {query!r} returned no results")

    logger.info("DuckDuckGo search for %r returned %d chars of results", query, len(output))
    return output
