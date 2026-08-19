"""Code Execution tool — runs Python in a locally sandboxed subprocess.

No MCP server is used here (see docs/MCP_NOTES.md for the full writeup
of why). Short version: the most legitimate option, pydantic's
mcp-run-python, was archived in January 2026 with two documented,
unpatched CVEs — a sandbox escape into the host JS runtime and an SSRF
via loopback network access — unacceptable for the one tool that runs
arbitrary LLM-generated code. Its intended successor, Monty, has no MCP
server yet and is explicitly "not ready for prime time" per its own
maintainers. Docker-socket-based servers need privileged access typical
free-tier deployment hosts don't offer.

Per the project's own documented fallback policy (implement the tool
with the same interface, note the fallback honestly in the README),
this is a self-built, defense-in-depth sandboxed runner instead:
  - A fresh `python -I` subprocess per call (isolated mode: ignores
    PYTHON* env vars / PYTHONPATH injection, no user site-packages)
  - A minimal environment (no inherited secrets — GROQ_API_KEY etc.
    never reach the executed code)
  - The snippet runs *inside* file_system's own sandbox dir (see below)
    rather than a fully isolated temp dir
  - A hard wall-clock timeout; the subprocess is killed on expiry
  - Output capped so a print-bomb can't flood the response
  - An AST pre-check rejecting code that imports os/subprocess/socket/
    etc. or calls eval/exec/__import__, before a subprocess is even
    spawned for it

Shares file_system's sandbox directory: originally this
ran in a fully isolated throwaway temp dir with `open()` denied outright,
meaning code_execution could never see a file file_system had just
written (or another file_system step needs to read) -- "search data, save
data.json, then write and run a script that reads data.json" failed every
time, confirmed live 3 separate ways (cities.csv,
repos.json/analysis.py, and a multi-file import case). Fixed
by running the snippet file *in* `settings.files_sandbox_dir` itself
(same directory file_system.py's MCP server writes to) instead of a
disconnected temp dir, and un-denying `open`. This is a deliberate trust
boundary decision, not a smaller one snuck in: code_execution's
LLM-generated code can now read/write anything file_system's own MCP
server already could -- the *same* boundary, not a wider one. What it
does NOT get: `os`/`subprocess`/`socket`/etc. stay denied (unrelated to
file I/O, still real defense-in-depth against process/network access).

HONEST LIMITATION: this is not a true OS-level sandbox — no seccomp, no
network namespace, no cgroup memory/CPU limits, and no real
path-confinement enforcement the way file_system.py's MCP server has (it
delegates that to a battle-tested library rather than hand-rolled path
checks, precisely to avoid CVE-2025-53110-style bugs — see
file_system.py's own docstring). `_check_no_path_escape` below is a
static, best-effort speed bump (rejects the *obvious* `../` or absolute-
path literals passed to `open()`/`Path(...)`), not equivalent protection
— a sufficiently adversarial payload constructing a path string at
runtime instead of a literal would not be caught. Accepted deliberately,
same reasoning already documented below for the import denylist: the
threat model here is LLM-generated code from this project's own trusted
pipeline, not adversarial third-party input, and file_system's MCP
server already grants the *same* directory the *same* read/write access
through a different (already-trusted) path.

Uniform interface with the other 2 tools: async run(code) -> str,
raising CodeExecutionError on failure.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

from src.config import get_settings

logger = logging.getLogger("taskpilot.tools.code_execution")

_DEFAULT_TIMEOUT_SECONDS = 10.0
_MAX_OUTPUT_CHARS = 20_000

# Checked at the AST level, before a subprocess is even spawned. Not a
# real security boundary on its own (see the module docstring) — a fast,
# clear speed bump against the obviously dangerous stuff an LLM might
# generate, on top of the subprocess/env/timeout isolation below.
_DENIED_IMPORTS = {
    "os", "subprocess", "socket", "shutil", "ctypes",
    "multiprocessing", "threading", "importlib", "urllib", "http",
    "ftplib", "smtplib", "pickle", "builtins",
}
# `sys` was denied originally but found overly restrictive live: it blocks
# harmless, common code (sys.exit(), sys.argv) for no real safety gain --
# the actually dangerous surface (process/filesystem/network access) is
# already covered by os/subprocess/socket above. sys._getframe()-style
# introspection gadgets remain a theoretical residual risk, same caveat
# as the module docstring already states about this denylist not being a
# real security boundary on its own.
#
# `open` was denied originally too, but became legitimate once the
# subprocess started running inside file_system's own sandbox dir (see module
# docstring), so `open('data.json')` reading a file file_system actually
# saved is the whole point now, not a hole. Path *escape* (`../`,
# absolute paths) is still checked separately -- see
# _check_no_path_escape below.
_DENIED_CALLS = {"eval", "exec", "compile", "__import__", "input", "breakpoint"}

# Best-effort: a string literal passed straight to open(...) or
# Path(...)/pathlib.Path(...) that looks like it's trying to leave the
# sandbox dir. Only catches literals, not a path built at runtime (see
# the module docstring's HONEST LIMITATION) -- same "speed bump, not a
# real boundary" spirit as the import denylist above.
_PATH_ESCAPE_MARKERS = ("..",)


def _looks_like_path_escape(path_str: str) -> bool:
    if any(marker in path_str for marker in _PATH_ESCAPE_MARKERS):
        return True
    p = Path(path_str)
    # An absolute path (POSIX '/...' or Windows 'C:\...') reaches outside
    # the sandbox dir regardless of what cwd is.
    return p.is_absolute()


# Every module this interpreter's standard library actually has -- nothing
# else is installed in the sandbox (no pygame, no numpy, no requests, ...).
# Found necessary live: the LLM generating code has no way to know
# what's actually installed, so it reaches for the obvious real-world
# choice (pygame for a game, requests for HTTP) and only finds out via a
# raw ModuleNotFoundError traceback after a subprocess already ran --
# confusing, and indistinguishable from a real bug in the generated code.
# Checking against the *actual* interpreter's module list here (not a
# hand-maintained guess) means this can never drift out of sync with what
# python -I really has available.
_STDLIB_MODULES = sys.stdlib_module_names


class CodeExecutionError(RuntimeError):
    """Raised when code fails the safety pre-check, times out, or exits non-zero."""


def _is_local_sandbox_module(root: str, sandbox_dir: Path) -> bool:
    """True if `root` is a file/package another step already saved in the
    sandbox dir (e.g. file_system wrote data_loader.py earlier) -- a
    legitimate local import, not a missing third-party package. Checked
    against the real directory, not guessed, so it can't go stale."""
    return (sandbox_dir / f"{root}.py").is_file() or (sandbox_dir / root / "__init__.py").is_file()


def _check_code_is_safe(code: str, sandbox_dir: Path) -> None:
    """AST-based pre-check. Raises CodeExecutionError naming the first
    denied import/call it finds, without ever spawning a subprocess."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise CodeExecutionError(f"Code has a syntax error: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _DENIED_IMPORTS:
                    raise CodeExecutionError(f"Import of {alias.name!r} is not allowed")
                if root not in _STDLIB_MODULES and not _is_local_sandbox_module(root, sandbox_dir):
                    raise CodeExecutionError(_not_installed_message(alias.name))
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _DENIED_IMPORTS:
                raise CodeExecutionError(f"Import of {node.module!r} is not allowed")
            if root and root not in _STDLIB_MODULES and not _is_local_sandbox_module(root, sandbox_dir):
                raise CodeExecutionError(_not_installed_message(node.module or ""))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in _DENIED_CALLS:
                    raise CodeExecutionError(f"Call to {func.id}(...) is not allowed")
                if func.id in ("open", "Path"):
                    _check_first_arg_is_not_a_path_escape(node, func.id)
            elif isinstance(func, ast.Attribute) and func.attr == "Path":
                # pathlib.Path(...) -- func is an Attribute (pathlib.Path),
                # not a bare Name, since it's accessed off the module.
                _check_first_arg_is_not_a_path_escape(node, "Path")


def _check_first_arg_is_not_a_path_escape(node: ast.Call, call_name: str) -> None:
    """Only checks a literal string first argument -- a path built at
    runtime isn't caught, see the module docstring's HONEST LIMITATION."""
    if not node.args:
        return
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        if _looks_like_path_escape(first.value):
            raise CodeExecutionError(
                f"{call_name}({first.value!r}, ...) is not allowed: paths must stay inside the "
                "sandbox directory -- no '..' and no absolute paths."
            )


def _not_installed_message(module_name: str) -> str:
    return (
        f"Import of {module_name!r} is not allowed: this sandbox only has Python's standard "
        "library available -- no third-party packages (pygame, numpy, requests, etc.) are "
        "installed. Code needing one of those can be saved with file_system, but code_execution "
        "can't run it here."
    )


def _sandbox_dir() -> Path:
    """Same directory file_system.py's MCP server writes to -- re-read
    each call (not cached) so tests can override FILES_SANDBOX_DIR, same
    reasoning as file_system.py's _server_args(). Created if missing."""
    sandbox = Path(get_settings().files_sandbox_dir).resolve()
    sandbox.mkdir(parents=True, exist_ok=True)
    return sandbox


async def run(code: str, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> str:
    """Run `code` in a Python subprocess, return its stdout.

    The subprocess's cwd is file_system's own sandbox dir (see
    module docstring), so code that reads/writes a file there (one
    file_system itself saved, or one this same code writes for a later
    step to use) actually works. The code must still `print()` whatever
    it wants returned to the caller -- stdout is the only channel back
    into the agent's own state.

    Raises CodeExecutionError on an unsafe-code pre-check failure, a
    timeout, or a non-zero exit (stderr folded into the message).
    """
    if not code.strip():
        raise CodeExecutionError("Empty code")

    sandbox = _sandbox_dir()
    _check_code_is_safe(code, sandbox)

    # A unique, clearly-scoped filename -- not a random temp dir elsewhere,
    # so a relative open('data.json') resolves against the sandbox dir via
    # cwd below. Deleted in `finally` regardless of outcome so it never
    # lingers as a stray file mixed in with the user's real ones.
    script_path = sandbox / f"_taskpilot_exec_{uuid.uuid4().hex}.py"

    # `python -I` deliberately does NOT add the script's own directory to
    # sys.path (a real security property of isolated mode, unlike plain
    # `python script.py`) and ignores PYTHONPATH too (implied by -I -> -E)
    # -- so neither of the two normal ways to make sibling-file imports
    # work (like "import data_loader") are available here. This line adds
    # *only* the sandbox dir, explicitly, keeping every other isolation
    # property -I gives up. Shifts real code down by one line in
    # tracebacks -- a minor, accepted cosmetic cost.
    bootstrap = f"import sys as _sys; _sys.path.insert(0, {str(sandbox)!r})\n"
    script_path.write_text(bootstrap + code, encoding="utf-8")

    try:
        env = {"SystemRoot": os.environ.get("SystemRoot", "")} if sys.platform == "win32" else {}
        # Predictable decoding regardless of the host's console codepage
        # (a real Windows gotcha with non-ASCII print() output).
        env["PYTHONIOENCODING"] = "utf-8"

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(script_path),
            cwd=str(sandbox),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise CodeExecutionError(f"Code timed out after {timeout}s") from exc
    finally:
        script_path.unlink(missing_ok=True)

    stdout = stdout_bytes.decode("utf-8", errors="replace")[:_MAX_OUTPUT_CHARS]
    stderr = stderr_bytes.decode("utf-8", errors="replace")[:_MAX_OUTPUT_CHARS]

    if proc.returncode != 0:
        raise CodeExecutionError(f"Code exited with status {proc.returncode}: {stderr or stdout}")

    logger.info("Executed %d chars of code, produced %d chars of output", len(code), len(stdout))
    return stdout
