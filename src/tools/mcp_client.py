"""MCP connection manager: spawn an MCP server over stdio, list tools, call tools.

Generic on purpose — not specific to Web Search. File System and Code
Execution reuse this same manager to talk to their own MCP
servers, which is what keeps all 3 tool wrappers uniform.

Each call spawns a fresh subprocess and tears it down afterward — simple
and stateless, which is fine for the request-per-tool-call volume this
agent needs. No persistent server pool to manage or leak.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("taskpilot.tools.mcp_client")


class McpToolError(RuntimeError):
    """Raised when spawning the server, calling a tool, or the tool itself fails.

    Callers (the tool wrappers in src/tools/*.py) catch this one type
    instead of having to know about MCP's own multi-layered exceptions.
    """


@asynccontextmanager
async def mcp_session(command: str, args: Sequence[str] = ()):
    """Spawn `command args...` as an MCP server subprocess, yield a live ClientSession.

    Usage: `async with mcp_session("uvx", ["duckduckgo-mcp-server"]) as session: ...`
    """
    params = StdioServerParameters(command=command, args=list(args))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def list_tools(command: str, args: Sequence[str] = ()) -> list[str]:
    """Debug/introspection helper: what tools does this MCP server expose?"""
    async with mcp_session(command, args) as session:
        result = await session.list_tools()
        return [tool.name for tool in result.tools]


async def call_tool(
    command: str, args: Sequence[str], tool_name: str, tool_args: dict
) -> str:
    """Spawn the server, call one tool, return its text output, tear the server down.

    Raises McpToolError on any failure — bad tool name, server crash, or
    the tool itself reporting an error (`result.is_error`).

    IMPORTANT: raising while still inside `async with mcp_session(...)` gets
    caught by anyio's TaskGroup and re-wrapped as an ExceptionGroup by the
    time it reaches the caller — `except McpToolError` in src/tools/*.py
    would silently NOT catch it (found live testing a sandbox
    escape attempt against the filesystem tool; earlier fake-based tests
    never exercised this path). So: gather the result inside the `async
    with`, raise only after it's closed.
    """
    text = ""
    is_error = False
    call_error: Exception | None = None

    async with mcp_session(command, args) as session:
        try:
            result = await session.call_tool(tool_name, tool_args)
        except Exception as exc:  # noqa: BLE001 - any MCP/transport failure -> one clear type
            call_error = exc
        else:
            text = _extract_text(result)
            is_error = result.is_error

    if call_error is not None:
        raise McpToolError(f"MCP call to {tool_name!r} failed: {call_error}") from call_error
    if is_error:
        raise McpToolError(f"{tool_name!r} reported an error: {text}")
    return text


def _extract_text(result) -> str:
    """MCP tool results are a list of content blocks; this agent only deals in text."""
    parts = [block.text for block in result.content if getattr(block, "type", None) == "text"]
    return "\n".join(parts)
