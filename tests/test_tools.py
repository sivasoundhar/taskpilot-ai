"""The Web Search tool wrapper (src/tools/web_search.py).

Unit tests inject a fake `_call_tool` (no subprocess spawn, no network),
so these are fast and deterministic — mirrors the fake-LLM pattern in
test_planner.py. One live smoke test actually spawns
`uvx duckduckgo-mcp-server` and hits real DuckDuckGo; it's skipped
automatically if `uvx` isn't on PATH, and is inherently slower/flakier
than the unit tests since it depends on the network.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.config import get_settings
from src.tools.code_execution import CodeExecutionError
from src.tools.code_execution import run as run_code
from src.tools.file_system import FileSystemError
from src.tools.file_system import read as fs_read
from src.tools.file_system import write as fs_write
from src.tools.mcp_client import McpToolError
from src.tools.web_search import WebSearchError, run


async def _fake_call_tool_success(command, args, tool_name, tool_args):
    assert command == "uvx"
    assert tool_name == "search"
    assert "query" in tool_args
    return "1. Example Result\n   https://example.com\n   A relevant snippet."


async def _fake_call_tool_empty(_command, _args, _tool_name, _tool_args):
    return ""


async def _fake_call_tool_error(_command, _args, _tool_name, _tool_args):
    raise McpToolError("server unreachable")


async def test_search_returns_results():
    result = await run("latest AI news", _call_tool=_fake_call_tool_success)
    assert "Example Result" in result
    assert "https://example.com" in result


async def test_empty_query_raises_without_calling_the_server():
    called = False

    async def _spy(*_args, **_kwargs):
        nonlocal called
        called = True
        return "should not be reached"

    with pytest.raises(WebSearchError):
        await run("   ", _call_tool=_spy)
    assert called is False


async def test_empty_search_results_raise_web_search_error():
    with pytest.raises(WebSearchError):
        await run("a query with genuinely no results", _call_tool=_fake_call_tool_empty)


async def test_mcp_failure_is_wrapped_as_web_search_error():
    """A raw McpToolError (server crash, transport failure, ...) never
    leaks past the tool wrapper — callers only ever see WebSearchError."""
    with pytest.raises(WebSearchError):
        await run("anything", _call_tool=_fake_call_tool_error)


@pytest.mark.skipif(shutil.which("uvx") is None, reason="uvx not installed")
async def test_search_live_smoke_test():
    """Real subprocess (`uvx duckduckgo-mcp-server`) + real DuckDuckGo call.
    No API key needed. Confirms the MCP server is actually wired up end
    to end, not just that the fakes are self-consistent."""
    result = await run("Python programming language", max_results=3)
    assert len(result.strip()) > 0


# ---------------------------------------------------------------------------
# Web Search: Tavily (primary when TAVILY_API_KEY is set). The autouse
# _disable_tavily_by_default fixture
# (conftest.py) keeps every test above this point on the original
# DuckDuckGo-only path; these tests re-enable Tavily explicitly.
# ---------------------------------------------------------------------------


def _enable_tavily(monkeypatch, api_key: str = "fake-tavily-key") -> None:
    monkeypatch.setattr(get_settings(), "tavily_api_key", api_key, raising=False)


async def _fake_tavily_search_success(_query, _max_results, _api_key):
    return {
        "results": [
            {"title": "Example Result", "url": "https://example.com", "content": "A relevant snippet."},
        ]
    }


async def _fake_tavily_search_empty(_query, _max_results, _api_key):
    return {"results": []}


async def _fake_tavily_search_error(_query, _max_results, _api_key):
    raise ConnectionError("tavily unreachable")


async def test_tavily_is_used_when_configured(monkeypatch):
    _enable_tavily(monkeypatch)
    result = await run("latest AI news", _tavily_search=_fake_tavily_search_success)
    assert "Example Result" in result
    assert "https://example.com" in result


async def test_tavily_failure_falls_back_to_duckduckgo(monkeypatch):
    """A broken Tavily call must never surface as an error to the caller
    -- it falls through to DuckDuckGo silently, same tool-fails-gracefully
    principle applied one layer earlier."""
    _enable_tavily(monkeypatch)
    result = await run(
        "latest AI news",
        _tavily_search=_fake_tavily_search_error,
        _call_tool=_fake_call_tool_success,
    )
    assert "Example Result" in result  # the DuckDuckGo fake's result, not Tavily's


async def test_tavily_empty_results_falls_back_to_duckduckgo(monkeypatch):
    _enable_tavily(monkeypatch)
    result = await run(
        "a query with genuinely no results",
        _tavily_search=_fake_tavily_search_empty,
        _call_tool=_fake_call_tool_success,
    )
    assert "Example Result" in result


async def test_tavily_not_used_when_no_key_configured():
    """Confirms the autouse fixture actually does its job: with no key,
    the DuckDuckGo fake is the only thing that can produce this result."""
    result = await run("latest AI news", _call_tool=_fake_call_tool_success)
    assert "Example Result" in result


@pytest.mark.skipif(not get_settings().tavily_api_key, reason="requires TAVILY_API_KEY")
async def test_tavily_search_live(monkeypatch):
    """Real Tavily API call. Currently expected to fail in this dev
    environment due to a local network/TLS-interception issue blocking
    all outbound HTTPS -- not a code problem, kept
    as a real regression test for once that's resolved."""
    _enable_tavily(monkeypatch, get_settings().tavily_api_key)
    result = await run("Python programming language", max_results=3)
    assert len(result.strip()) > 0


# ---------------------------------------------------------------------------
# File System tool wrapper (src/tools/file_system.py)
#
# Same pattern: fake `_call_tool` unit tests (no subprocess spawn) plus live
# smoke tests that spawn the real `npx @modelcontextprotocol/server-filesystem`
# server and touch real files under the sandbox dir (./workspace, gitignored)
# -- skipped automatically if npx isn't on PATH.
# ---------------------------------------------------------------------------


async def _fake_fs_call_tool_write_ok(_command, _args, tool_name, tool_args):
    assert tool_name == "write_file"
    assert "path" in tool_args and "content" in tool_args
    return f"Successfully wrote to {tool_args['path']}"


async def _fake_fs_call_tool_read_ok(_command, _args, tool_name, _tool_args):
    assert tool_name == "read_text_file"
    return "file contents here"


async def _fake_fs_call_tool_access_denied(_command, _args, _tool_name, _tool_args):
    raise McpToolError("Access denied - path outside allowed directories")


async def test_write_returns_confirmation():
    result = await fs_write("report.txt", "hello world", _call_tool=_fake_fs_call_tool_write_ok)
    assert "report.txt" in result


async def test_read_returns_file_content():
    result = await fs_read("report.txt", _call_tool=_fake_fs_call_tool_read_ok)
    assert result == "file contents here"


async def test_empty_path_raises_without_calling_the_server():
    called = False

    async def _spy(*_args, **_kwargs):
        nonlocal called
        called = True
        return "should not be reached"

    with pytest.raises(FileSystemError):
        await fs_write("   ", "content", _call_tool=_spy)
    assert called is False

    with pytest.raises(FileSystemError):
        await fs_read("   ", _call_tool=_spy)
    assert called is False


async def test_invalid_path_is_wrapped_as_file_system_error():
    """A path-outside-sandbox rejection (McpToolError from the server) is
    surfaced as FileSystemError -- callers never see a raw McpToolError."""
    with pytest.raises(FileSystemError):
        await fs_write("../escape.txt", "x", _call_tool=_fake_fs_call_tool_access_denied)
    with pytest.raises(FileSystemError):
        await fs_read("../escape.txt", _call_tool=_fake_fs_call_tool_access_denied)


@pytest.mark.skipif(shutil.which("npx") is None, reason="npx not installed")
async def test_write_then_read_live_round_trip():
    """Real subprocess (npx @modelcontextprotocol/server-filesystem) +
    real writes/reads under the sandbox dir -- write() then read() must
    see each other's effect, not just each independently succeed."""
    filename = "file_system_live_test.txt"
    content = "TaskPilot live round-trip check"
    try:
        write_result = await fs_write(filename, content)
        assert filename in write_result

        read_result = await fs_read(filename)
        assert read_result == content
    finally:
        sandbox = Path(get_settings().files_sandbox_dir)
        (sandbox / filename).unlink(missing_ok=True)


@pytest.mark.skipif(shutil.which("npx") is None, reason="npx not installed")
async def test_sandbox_escape_is_blocked_live():
    """Real subprocess: an absolute path outside the sandbox must be
    rejected by the server, not silently allowed. Regression check for
    CVE-2025-53109/53110-style sandbox escapes (see docs/MCP_NOTES.md)."""
    outside_path = str(Path(__file__).resolve().parent.parent / "README.md")
    with pytest.raises(FileSystemError):
        await fs_read(outside_path)


@pytest.mark.skipif(shutil.which("npx") is None, reason="npx not installed")
async def test_read_missing_file_is_file_system_error_live():
    with pytest.raises(FileSystemError):
        await fs_read("this_file_does_not_exist_day4.txt")


# ---------------------------------------------------------------------------
# Code Execution tool (src/tools/code_execution.py)
#
# No MCP server / subprocess-spawn distinction here -- this tool has zero
# network dependency (it's a local Python subprocess), so every test just
# calls run() directly. No fakes needed, and nothing to skip.
# ---------------------------------------------------------------------------


async def test_basic_arithmetic_returns_correct_output():
    result = await run_code("print(2 + 2)")
    assert result.strip() == "4"


async def test_data_analysis_snippet_works():
    code = "data = [1, 4, 9, 16, 25]\nprint(sum(data) / len(data))\n"
    result = await run_code(code)
    assert result.strip() == "11.0"


async def test_empty_code_raises_without_spawning_a_subprocess():
    with pytest.raises(CodeExecutionError):
        await run_code("   ")


async def test_syntax_error_is_handled_not_a_crash():
    with pytest.raises(CodeExecutionError):
        await run_code("this is not valid python (((")


async def test_runtime_exception_is_handled_not_a_crash():
    """A ZeroDivisionError inside the executed code must surface as a clean
    CodeExecutionError in *our* process, not propagate as a raw exception
    or take the whole agent down."""
    with pytest.raises(CodeExecutionError) as exc_info:
        await run_code("1 / 0")
    assert "ZeroDivisionError" in str(exc_info.value)


async def test_timeout_on_infinite_loop():
    with pytest.raises(CodeExecutionError) as exc_info:
        await run_code("while True:\n    pass\n", timeout=2)
    assert "timed out" in str(exc_info.value).lower()


@pytest.mark.parametrize(
    "code",
    [
        "import subprocess\nsubprocess.run(['echo', 'hi'])",
        "import os\nos.system('echo hi')",
        "import socket\nsocket.socket()",
        "eval('1+1')",
        "__import__('os').system('echo hi')",
    ],
)
async def test_dangerous_patterns_are_blocked_before_a_subprocess_spawns(code):
    with pytest.raises(CodeExecutionError):
        await run_code(code)


async def test_multi_line_program_with_a_function_works():
    code = (
        "def compound_interest(principal, rate, years):\n"
        "    return principal * (1 + rate) ** years\n"
        "print(round(compound_interest(10000, 0.05, 10), 2))\n"
    )
    result = await run_code(code)
    assert result.strip() == "16288.95"


async def test_sys_import_is_allowed():
    """`sys` was denied originally but found overly restrictive live --
    it blocks harmless, common code (sys.exit(), sys.version_info) for
    no real safety gain; the actually dangerous surface is os/subprocess/
    socket, already denied."""
    result = await run_code("import sys\nprint(sys.version_info.major)\n")
    assert result.strip() == "3"


# ---------------------------------------------------------------------------
# code_execution shares file_system's sandbox dir (previously a
# fully isolated throwaway temp dir) -- "search data, save data.json, then
# write and run a script that reads data.json" failed every time before
# this, confirmed live 3 separate ways (cities.csv, repos.json/analysis.py,
# a multi-file import case). open() is legitimate now; path escapes
# (../, absolute paths) are still checked.
# ---------------------------------------------------------------------------


async def test_code_execution_can_read_a_file_file_system_saved():
    """The actual regression: code_execution used to have zero access to
    anything file_system had written."""
    sandbox = Path(get_settings().files_sandbox_dir)
    sandbox.mkdir(parents=True, exist_ok=True)
    filename = "day9_live_test_data.txt"
    (sandbox / filename).write_text("42", encoding="utf-8")
    try:
        result = await run_code(f"print(open({filename!r}).read())")
        assert result.strip() == "42"
    finally:
        (sandbox / filename).unlink(missing_ok=True)


async def test_code_execution_can_import_a_file_file_system_saved():
    """The multi-file case (data_loader.py imported by a later step) --
    not just open(), a real Python import of a sibling file."""
    sandbox = Path(get_settings().files_sandbox_dir)
    sandbox.mkdir(parents=True, exist_ok=True)
    helper_name = "day9_live_test_helper.py"
    (sandbox / helper_name).write_text("def double(x):\n    return x * 2\n", encoding="utf-8")
    try:
        result = await run_code("import day9_live_test_helper as helper\nprint(helper.double(21))")
        assert result.strip() == "42"
    finally:
        (sandbox / helper_name).unlink(missing_ok=True)


async def test_code_execution_writes_are_visible_to_a_later_call():
    """Two separate run() calls, like two separate agent steps -- the
    second must see what the first wrote, same directory both times."""
    sandbox = Path(get_settings().files_sandbox_dir)
    filename = "day9_live_test_roundtrip.txt"
    try:
        await run_code(f"open({filename!r}, 'w').write('hello')")
        result = await run_code(f"print(open({filename!r}).read())")
        assert result.strip() == "hello"
    finally:
        (sandbox / filename).unlink(missing_ok=True)


@pytest.mark.parametrize(
    "code",
    [
        "open('../escape.txt', 'w')",
        "open('/etc/passwd')",
        "from pathlib import Path\nPath('../escape.txt').write_text('x')",
        "import pathlib\npathlib.Path('../escape.txt').write_text('x')",
    ],
)
async def test_path_escape_attempts_are_still_blocked(code):
    with pytest.raises(CodeExecutionError):
        await run_code(code)


# ---------------------------------------------------------------------------
# Non-stdlib imports (pygame, numpy, requests, ...): a real regression.
# The sandbox never had these installed, but the failure only ever
# surfaced as a raw ModuleNotFoundError traceback *after* a subprocess
# already ran -- confusing, indistinguishable from a real bug in the
# generated code, and found repeatedly live (pong.py, Flappy_Bird.py)
# once game-writing tasks started reaching for pygame.
# ---------------------------------------------------------------------------


async def test_third_party_import_is_rejected_before_a_subprocess_spawns():
    with pytest.raises(CodeExecutionError) as exc_info:
        await run_code("import pygame\npygame.init()\n")
    assert "pygame" in str(exc_info.value)
    assert "standard library" in str(exc_info.value)


async def test_third_party_from_import_is_also_rejected():
    with pytest.raises(CodeExecutionError) as exc_info:
        await run_code("from numpy import array\nprint(array([1, 2, 3]))\n")
    assert "numpy" in str(exc_info.value)


@pytest.mark.parametrize("module", ["json", "math", "random", "datetime", "collections", "itertools"])
async def test_common_stdlib_imports_still_work(module):
    """The stdlib-only check must not be so broad it starts rejecting
    ordinary standard-library modules the agent legitimately needs."""
    result = await run_code(f"import {module}\nprint('ok')\n")
    assert result.strip() == "ok"
