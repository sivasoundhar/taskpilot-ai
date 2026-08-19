"""File System tool — wraps the official filesystem MCP server.

MCP server used: `@modelcontextprotocol/server-filesystem` (npm), run
on-demand via `npx -y @modelcontextprotocol/server-filesystem@<version>
<sandbox_dir>` over stdio. Pinned to 2026.7.10 rather than trusting
"latest" implicitly — versions before 2025.7.1 had a real sandbox-escape
bug (CVE-2025-53109 symlink bypass, CVE-2025-53110 prefix-matching
bypass); see docs/MCP_NOTES.md.

Safety: every read/write is confined to `settings.files_sandbox_dir`. The
server itself enforces the boundary — this wrapper deliberately does NOT
duplicate path validation, since two independent implementations of "is
this path inside the sandbox" is how CVE-2025-53110-style bugs happen in
the first place. Verified live: an absolute path outside the
sandbox and a `..` traversal were both rejected by the server with
"Access denied - path outside allowed directories". Relative paths (e.g.
"report.txt") resolve against the sandbox dir automatically — also
verified live — so callers never need to build absolute paths themselves.

Uniform interface with the other 2 tools (src/tools/web_search.py,
code_execution.py): async functions returning str, raising a
single tool-specific error type on failure.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from src.config import get_settings
from src.tools.mcp_client import McpToolError, call_tool

logger = logging.getLogger("taskpilot.tools.file_system")

_SERVER_PACKAGE = "@modelcontextprotocol/server-filesystem@2026.7.10"

# Matches mcp_client.call_tool's signature. Injectable so tests can swap in
# a fake and skip spawning a real subprocess — same pattern as
# planner.py's injectable `llm` and web_search.py's injectable `_call_tool`.
CallTool = Callable[[str, Sequence[str], str, dict], Awaitable[str]]


class FileSystemError(RuntimeError):
    """Raised when a read/write fails: outside the sandbox, missing file, server error, etc."""


def _server_args() -> list[str]:
    """Re-reads settings each call (not cached at import time) so tests can
    override FILES_SANDBOX_DIR. Creates the sandbox dir if it doesn't exist
    yet — a write shouldn't fail just because nothing has been saved there."""
    sandbox = Path(get_settings().files_sandbox_dir).resolve()
    sandbox.mkdir(parents=True, exist_ok=True)
    return ["-y", _SERVER_PACKAGE, str(sandbox)]


async def write(path: str, content: str, *, _call_tool: CallTool = call_tool) -> str:
    """Write `content` to `path` inside the sandbox (relative paths preferred).

    Raises FileSystemError if `path` resolves outside the sandbox, or the
    server otherwise fails.
    """
    if not path.strip():
        raise FileSystemError("Empty file path")

    try:
        result = await _call_tool("npx", _server_args(), "write_file", {"path": path, "content": content})
    except McpToolError as exc:
        raise FileSystemError(f"Failed to write {path!r}: {exc}") from exc

    logger.info("Wrote %d chars to %r", len(content), path)
    return result


async def read(path: str, *, _call_tool: CallTool = call_tool) -> str:
    """Read and return the text content of `path` from the sandbox.

    Raises FileSystemError if `path` resolves outside the sandbox, doesn't
    exist, or the server otherwise fails.
    """
    if not path.strip():
        raise FileSystemError("Empty file path")

    try:
        content = await _call_tool("npx", _server_args(), "read_text_file", {"path": path})
    except McpToolError as exc:
        raise FileSystemError(f"Failed to read {path!r}: {exc}") from exc

    logger.info("Read %d chars from %r", len(content), path)
    return content
