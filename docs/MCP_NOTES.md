# MCP Server Notes

Which real MCP servers TaskPilot AI connects to, and why — verified directly
rather than trusting stale package names, since the MCP ecosystem moves fast.

---

## Web Search — `duckduckgo-mcp-server`, with an optional Tavily primary

- **Package:** [`duckduckgo-mcp-server`](https://github.com/nickclyde/duckduckgo-mcp-server) (PyPI, released 2026-07-01)
- **Run via:** `uvx duckduckgo-mcp-server` — spawned on demand as a stdio subprocess, one per call (see `src/tools/mcp_client.py`). No persistent server process to manage.
- **Transport:** stdio (default)
- **Tools exposed:** `search(query, max_results, region)`, `fetch_content(url, ...)` — TaskPilot only uses `search`.
- **API key:** **none required.** Uses DuckDuckGo's keyless HTML endpoint. This is why it was picked over the historical reference implementation (Brave Search MCP server), which needs a paid/keyed API — keeping the whole project free and key-free by default.
- **Verification performed:**
  - `uvx duckduckgo-mcp-server --help` — confirmed it launches, lists CLI flags, no key prompts.
  - Live `search` calls via `src/tools/web_search.py` — both standalone (`tests/test_tools.py::test_search_live_smoke_test`) and end-to-end through the full planner → executor path with a real goal.
  - Verified again from inside the built Docker image (`docker exec ... python -c "...web_search.run(...)"`) — confirms the container has network egress and `uv`/`uvx` available at runtime, not just at build time.
- **Runtime dependency added:** `uv`/`uvx` must be on PATH. Added to `Dockerfile` (`pip install uv`, plus a build-time `uvx duckduckgo-mcp-server --help` to warm the cache so the first live request isn't stuck downloading the server package) and documented in `README.md` setup.

**Fallback (not currently needed):** the project's fallback policy allows implementing a tool as a plain LangChain tool if its MCP server proves unstable. Not exercised — `duckduckgo-mcp-server` has worked in every check above. If it ever becomes flaky/unavailable, `src/tools/web_search.py`'s `run()` signature (`async def run(query, max_results) -> str`, raising `WebSearchError`) is the contract to preserve; only the internals (currently `mcp_client.call_tool`) would need to change.

**Tavily as an optional second backend:** Tavily (https://tavily.com) is a direct HTTP API (`tavily-python` SDK), **not** an MCP server — worth being explicit that this is a deliberate second, non-MCP backend for the same tool, not a replacement of the MCP one. Used as the *primary* when `TAVILY_API_KEY` is configured; falls back to DuckDuckGo automatically on a missing key or any Tavily-side failure (network error, empty results, etc.) — the project still runs entirely free/key-free by default for anyone without a Tavily key. Fallback logic covered by fake-based unit tests (`tests/test_tools.py`).

**Known issue:** `test_tavily_search_live` can fail in some local environments even when Tavily itself is fine, for a narrower reason: DuckDuckGo's MCP server is launched via `uvx` (Rust, not Python), which has its own separate TLS cert store from Python's — an intercepting proxy/cert setup that Python trusts fine can still make `uv`'s installer reject `duckduckgo-mcp-server`'s download with `invalid peer certificate: UnknownIssuer`. Only surfaces when Tavily itself is forced to fail first (this test's fallback-path scenario); the everyday case (Tavily configured and reachable) is unaffected. `uv`'s own `--system-certs` (or `UV_NATIVE_TLS=true`) flag is the fix if it comes up.

---

## File System — `@modelcontextprotocol/server-filesystem`

- **Package:** [`@modelcontextprotocol/server-filesystem`](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem) (npm) — the official reference filesystem server.
- **Run via:** `npx -y @modelcontextprotocol/server-filesystem@2026.7.10 <sandbox_dir>` — spawned on demand as a stdio subprocess (same pattern as Web Search). The sandbox directory is passed as a positional CLI arg; the server enforces that every operation stays inside it.
- **Transport:** stdio
- **Tools exposed:** `read_text_file`, `read_media_file`, `read_multiple_files`, `write_file`, `edit_file`, `create_directory`, `list_directory`, `list_directory_with_sizes`, `move_file`, `search_files`, `directory_tree`, `get_file_info`, `list_allowed_directories`. TaskPilot uses `write_file` and `read_text_file` — matches the project's uniform `read(path)`/`write(path, content)` tool interface.
- **API key:** none required — pure local filesystem access, no external service.
- **⚠️ Version pinned deliberately, not "latest":** versions before `2025.7.1` / `0.6.3` had two real vulnerabilities —
  - **CVE-2025-53110** (CVSS 7.3): naive prefix-matching directory check (`path.startsWith(allowedDir)`) let `/allowed_dir_extra` pass a check meant for `/allowed_dir`.
  - **CVE-2025-53109** (CVSS 8.4): symlink validation had a catch-block bug that only checked the parent directory when symlink resolution failed, allowing writes anywhere on disk (up to code execution via e.g. macOS Launch Agents).
  - Pinned to `2026.7.10` (well past the `2025.7.1` fix) rather than trusting an unpinned `npx -y ...@latest`, so a future bad release can't silently regress this.
- **Verification performed (live, not just "should work"):**
  - Listed tools via `mcp_client.list_tools()` — matches the README.
  - `write_file` then `read_text_file` round-trip, both via absolute and relative paths — relative paths (e.g. `"report.txt"`) resolve safely *inside* the sandbox automatically, so callers never build absolute paths themselves.
  - **Sandbox escape attempts, live** — both rejected with `"Access denied - path outside allowed directories"`:
    - Absolute path outside the sandbox
    - `..`-traversal from inside the sandbox
  - Missing-file read → clean `ENOENT`-based error, not a crash.
  - Re-verified from inside the rebuilt Docker image (`docker exec ... python -c "...file_system...write/read..."`) — confirms Node.js + `npx` work at container runtime, not just at build time.
- **Real bug found and fixed during this verification** (not specific to this server — a bug in TaskPilot's own `mcp_client.py`): raising `McpToolError` from inside `async with mcp_session(...)` gets caught by anyio's `TaskGroup.__aexit__` and re-wrapped into a Python `ExceptionGroup` by the time it reaches the caller — so `except McpToolError` in the tool wrappers silently did **not** catch it. Earlier tests only ever exercised the success path, so this never surfaced until a dedicated sandbox-escape test (which expects an error) was added. Fixed by moving all raising in `call_tool()` to after the `async with` block closes. Full test suite re-verified green after the fix.
- **Runtime dependency added:** Node.js/`npx` must be available (in addition to `uv`/`uvx`). Added to `Dockerfile` via a multi-stage `COPY --from=node:20-slim`, plus a build-time warm-up run so the first live request doesn't stall on an npm download.

**Fallback (not currently needed):** same as Web Search — not exercised, since this server has worked in every check above (once the sandbox pin + `mcp_client.py` bug were addressed). `src/tools/file_system.py`'s `read(path) -> str` / `write(path, content) -> str` contract is what to preserve if a fallback ever becomes necessary.

## Code Execution — self-built sandboxed subprocess (no MCP server — a deliberate decision)

Unlike Web Search and File System, this tool is **not** backed by a real MCP server — a deliberate, researched decision, not a shortcut. Every MCP option investigated had a real disqualifying problem:

| Option | Problem |
|---|---|
| `pydantic/mcp-run-python` (Pyodide + Deno) | The most legitimate option. **Archived January 2026.** Two documented, unpatched CVEs: **CVE-2026-25905** (sandbox escape — executed Python can reach Pyodide's `js` bridge into the host JS runtime) and **CVE-2026-25904** (SSRF — the Deno sandbox's network permissions allow loopback/internal access). Maintainers' own advice: migrate away. |
| `pydantic/monty` (the intended successor) | No MCP server exists for it — would mean building a custom MCP server, which is deliberately out of scope for this project (use existing servers, don't build one). Explicitly labeled "**experimental — not ready for prime time**" by its own maintainers. No third-party library support (by design). |
| `code-sandbox-mcp`, `llm-sandbox` (Docker-container-based) | Real isolation, but need the Docker socket mounted into our own container — a privilege escalation in itself, and typical free-tier hosts don't offer privileged Docker-in-Docker. |
| `mcp-python-exec-sandbox` (bubblewrap) | Linux-only (bubblewrap has no Windows equivalent) — can't even be dev-tested on this machine, and its container-runtime support for unprivileged user namespaces is unconfirmed on typical free-tier hosts. |

Worth calling out explicitly since it changes the "3 MCP tools" story: this uses the project's own documented fallback policy — implement the tool with the same interface, note the fallback honestly — rather than silently swapping in a fourth MCP server.

**What was built instead** (`src/tools/code_execution.py`): a fresh `python -I` subprocess per call —
- Isolated mode (`-I`): ignores `PYTHONPATH`/other `PYTHON*` env vars, no user site-packages
- Minimal environment passed to the subprocess (no inherited secrets — `GROQ_API_KEY` etc. never reach executed code)
- Disposable temp directory as `cwd`
- Hard wall-clock timeout (default 10s), subprocess killed on expiry
- Output capped at 20,000 chars (no print-bomb flooding the response)
- An AST pre-check rejecting `import os/sys/subprocess/socket/shutil/...` and calls to `eval/exec/open/__import__/...` — before a subprocess is even spawned

**Honest limitation, stated plainly (not overclaimed):** this is not a true OS-level sandbox. No seccomp, no network namespace, no cgroup memory/CPU limits. The AST denylist stops straightforward dangerous code but can't stop a sufficiently adversarial payload using Python object-graph gadgets (`().__class__.__mro__` chains) to reach live `subprocess.Popen` instances without ever writing `import subprocess`. Real protection against that needs the memory-isolated sandboxes ruled out above. Acceptable for this project's actual threat model — the executed code is generated by the agent's own LLM calls from goals the project owner supplies, not arbitrary third-party untrusted input — but this would need real containerized/WASM isolation before ever being exposed to untrusted external users.

**Verification performed (live):**
- `print(2 + 2)` → `4`; a small data-analysis snippet (mean of a list) → correct value
- Syntax error, runtime exception (`1/0`) → both caught cleanly as `CodeExecutionError`, no crash
- Infinite loop → killed after timeout, `CodeExecutionError` raised
- 6 dangerous-pattern variants (`subprocess`, `os.system`, `socket`, `eval`, `open`, `__import__`) → all blocked pre-spawn
- Full pipeline live: a real goal ("Calculate compound interest on 5000 at 4% for 6 years and save the result to a file") through `plan_goal()` → `execute_step()` for both steps → code_execution computed the correct value (6326.60, verified by hand: 5000 × 1.04⁶) → file_system saved it, read back and confirmed
- Re-verified inside a rebuilt Docker container — no new runtime dependency needed here (unlike Web Search's `uv`/File System's Node.js), since this tool just uses the same Python interpreter already in the image
