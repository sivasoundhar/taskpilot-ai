# Architecture

How TaskPilot AI is put together — the agent core, the 3 tools, the streaming
layer, and the trust boundaries between them. For *which* MCP servers back
each tool and why, see `docs/MCP_NOTES.md`.

---

## One-paragraph summary

A user's plain-English goal goes to an LLM-backed planner, which breaks it
into an ordered list of steps, each assigned to exactly one of 3 fixed tools
(web search, file system, code execution). An executor runs the steps in
order, routing each to its tool, chaining one step's real output into the
next step's prompt, and streaming a live update for every step's start and
finish over Server-Sent Events. The React frontend renders each update as a
step card the moment it arrives — the "watch the agent pick tool 1 → 2 → 3"
effect that's the whole point of the live UI.

---

## High-level flow

```
 User goal (plain English)
        |
        v
 POST /run  (src/main.py)
        |
        v
 run_agent()  (src/agent/graph.py)
        |
        +-- check_intent()  ---- chit-chat? --> yield ChatMessage, done
        |   (src/agent/planner.py)
        |
        v
 plan_goal()  --------------------------------------------> PlanningError
   (src/agent/planner.py)                                   --> "error" SSE event
        |  LLM produces steps, each tagged with exactly
        |  one tool; _validate_plan() fail-fasts a plan
        |  that reads a file nothing wrote first
        v
 run_plan()  (src/agent/executor.py)
        |
        |  for each step, in order:
        |    yield StepUpdate(RUNNING)
        |    execute_step()  --routes on step.tool-->  web_search.run()
        |                                          |    file_system.read()/write()
        |                                          |    code_execution.run()
        |    yield StepUpdate(DONE | FAILED)
        |    (code_execution: up to 3 attempts, error fed into the retry prompt)
        |    stop on the first still-failed step
        v
 yield TaskResult  ---->  storage.save_run()  (SQLite, backs /history)
        |
        v
 SSE stream  (src/utils/streaming.py)  --over HTTP-->  React frontend
        |
        v
 StepCard components render live, one per StepUpdate, as they arrive
```

---

## Components

### FastAPI layer — `src/main.py`

The only HTTP surface. Endpoints, request/response shapes, and SSE event
schemas are documented in `docs/API.md` rather than duplicated here — this
section is about what each endpoint *does*, not its wire format.

- `POST /run` is the actual product: streams `run_agent()`'s output over SSE.
- `GET /history` reads what `storage.py` persisted from past runs.
- `GET /settings` / `PATCH /settings` expose the few knobs
  `runtime_settings.py` allows changing live, without a restart.
- `GET /health` is a plain liveness check.

### Agent core — `src/agent/`

Four files, each with one job, composed by `graph.py`:

| File | Responsibility |
|---|---|
| `state.py` | `AgentState` — the `TypedDict` LangGraph threads through planning and execution (`goal`, `plan`, `current_step`, `results`, `status`). |
| `planner.py` | `check_intent()` (is this even a task, or just chat?) and `plan_goal()` (goal → ordered, tool-tagged steps), plus `_validate_plan()`'s fail-fast ordering check. |
| `executor.py` | `execute_step()` (routes one step to its tool, generates code/file content via LLM where needed, chains prior real output in) and `run_plan()` (drives the whole plan, yields `StepUpdate`s, owns the retry loop). |
| `graph.py` | Composes the two into one LangGraph graph (`build_agent_graph()`, non-streaming) and one async generator (`run_agent()`, the streaming path `POST /run` actually uses). |

**Why a two-stage plan-then-execute design, not a single ReAct-style loop
that plans one step at a time:** the live UI needs the *whole*
plan up front — it shows "3 steps identified" before any tool runs.
A single upfront plan also makes `_validate_plan()`'s ordering check
possible; a step-at-a-time loop would only discover a bad ordering by
actually hitting it mid-execution.

**LLM provider selection — `src/agent/llm_provider.py`:** every LLM call
in the agent (planning, intent-check, code generation, file-content
generation) goes through `resilient_llm()` / `resilient_structured_llm()`,
which wrap Groq (primary) and local Ollama (fallback) with LangChain's
`.with_fallbacks()` — any exception from the primary (auth, rate limit,
network) transparently retries against the fallback with the same input.
Centralized here so every call site gets identical behavior instead of
each hand-rolling its own `try`/`except`. The Settings page can flip which
one is tried first (`runtime_settings.py`'s `llm_provider_preference`) —
useful for demoing the fallback path deliberately.

### Tools — `src/tools/`

All 3 share one interface shape (`async def run(...)`/`read(...)`/`write(...)
-> str`, raising a tool-specific `*Error`) so `executor.py`'s routing logic
doesn't need to special-case any of them beyond which function to call.

- **`web_search.py`** — Tavily (if `TAVILY_API_KEY` is configured) as
  primary, falling back to the `duckduckgo-mcp-server` MCP server
  (key-free) on any Tavily failure or when no key is set.
- **`file_system.py`** — wraps the official
  `@modelcontextprotocol/server-filesystem` MCP server, sandboxed to
  `settings.files_sandbox_dir`. `read`/`write` is the same uniform interface
  shape all 3 tools share.
- **`code_execution.py`** — the one tool with **no MCP server behind it**
  (a researched, documented decision — every real option had a
  disqualifying problem; see `MCP_NOTES.md`). Self-built instead: a fresh
  `python -I` subprocess per call, AST-pre-checked for denied
  imports/calls before it's even spawned, timeout-bounded, output-capped.

**`mcp_client.py`** is the one piece both `web_search.py` and
`file_system.py` share: a generic "spawn an MCP server over stdio, call
one tool, tear it down" manager. Stateless by design — no persistent
server pool, a fresh subprocess per call, simple over clever at this
call volume.

### Streaming — `src/utils/streaming.py`

Turns what `run_agent()` yields (`StepUpdate` / `TaskResult` / `ChatMessage`)
into the `{"event": ..., "data": ...}` shape `sse-starlette`'s
`EventSourceResponse` expects. Kept separate from `main.py` so the endpoint
handler stays about orchestration, not serialization.

### Storage — `src/storage.py`

A small SQLite table (`data/history.db`) that `POST /run` writes to once a
task's final `TaskResult` arrives — never touched by the agent core itself,
so `graph.py`/`executor.py` stay side-effect-free and easy to test. Backs
the frontend's History page.

### Runtime settings — `src/runtime_settings.py`

Deliberately separate from `config.py`'s `Settings` (env-file-backed,
loaded once at startup). This is in-memory, live-editable session tuning —
LLM provider preference, log level, web search result count — reset to
`config.py`'s defaults on every restart by design, not a bug.

---

## State management

`AgentState` (`src/agent/state.py`) is a `TypedDict` with `total=False`
fields populated incrementally as the graph progresses:

```python
class AgentState(TypedDict, total=False):
    goal: str
    plan: list[PlannedStep]
    current_step: int
    results: list[StepResult]
    status: TaskStatus   # planning | planned | executing | done | failed
```

`run_plan()` mutates it in place — `results` grows by one `StepResult` per
completed step, which is also how chaining works: `execute_step()` reads
`state["results"][-1]` to pull the previous step's *real* output (not its
short confirmation message — see `_chain_context()`'s docstring) into the
next step's LLM prompt.

---

## Error handling & resilience

Three distinct mechanisms, each solving a different failure mode:

1. **LLM provider fallback** (`llm_provider.py`) — Groq → Ollama, for any
   LLM call anywhere in the agent. Handles *infrastructure* failures
   (outage, rate limit, network).
2. **Bounded retry for `code_execution` only** (`executor.py`,
   `_MAX_CODE_EXECUTION_ATTEMPTS = 3`) — a failed attempt's real error
   feeds into the next attempt's code-gen prompt, and `_retry_guidance()`
   tailors the advice to *what kind* of failure it was (a sandbox
   restriction gets "don't work around it," a real bug gets "fix the
   actual logic"). Scoped to `code_execution` deliberately — a failed
   `web_search`/`file_system` step is usually a config/infra problem,
   not something regenerating code can fix.
3. **Fail-fast plan validation** (`planner.py`'s `_validate_plan()`) —
   catches a plan that reads a file nothing writes first, before
   execution wastes real steps discovering it live.

**Overall execution policy:** stop on the first step still failed after
retries, rather than continuing past it. Later steps are usually chained
off earlier ones, so continuing would mean computing on missing data —
worse than stopping and reporting clearly.

---

## Trust boundaries — `code_execution`'s sandbox

Worth being explicit about, since it's the one tool without a battle-tested
MCP server behind it. `code_execution` runs LLM-generated Python inside
`file_system`'s own sandbox directory (they share `settings.files_sandbox_dir`
— a deliberate design decision, not a wider boundary snuck in: the code can
now read/write anything `file_system`'s MCP server already could, the
*same* boundary, not a bigger one). On top of that:

- `python -I` (isolated mode): ignores `PYTHONPATH`/other `PYTHON*` env
  vars, no user site-packages.
- An AST pre-check denies `os`/`subprocess`/`socket`/`shutil`/etc. imports
  and `eval`/`exec`/`__import__`/`input` calls, before a subprocess is
  even spawned.
- A best-effort literal-path-escape check on `open(...)`/`Path(...)` calls
  (`..`, absolute paths) — **stated honestly as a speed bump, not a real
  boundary**: it only catches string literals, not a path built at
  runtime.
- A hard wall-clock timeout and capped output.

**Explicitly not a real OS-level sandbox** — no seccomp, no network
namespace, no cgroup limits. Acceptable for this project's actual threat
model (LLM-generated code from the project's own pipeline, not arbitrary
third-party input) — would need real containerized/WASM isolation before
ever being exposed to untrusted external users. See `code_execution.py`'s
own module docstring and `MCP_NOTES.md` for the full reasoning, including
why every real MCP-based code-execution option was ruled out first.

---

## Frontend architecture

React 18 + TypeScript + Vite, proxying `/run`, `/health`, `/history`,
`/settings` to the FastAPI backend in dev (`vite.config.ts`).

```
App.tsx
 └─ pages/
     ├─ AgentPage.tsx     — TaskInput + live StepList + ResultDisplay
     ├─ HistoryPage.tsx   — past runs, from GET /history
     ├─ SettingsPage.tsx  — GET/PATCH /settings
     └─ ToolsPage.tsx     — static info on the 3 tools
 └─ components/
     ├─ TaskInput.tsx     — goal input box
     ├─ StepCard.tsx      — ⭐ one live tool-use card (icon + status)
     ├─ StepList.tsx      — sequence of StepCards, grows as events arrive
     ├─ ChatPanel.tsx     — renders a ChatMessage (non-task input)
     ├─ ExecutionPanel.tsx, ResultDisplay.tsx, Sidebar.tsx
 └─ hooks/useAgentRun.ts  — drives streamRun(), holds step/result state
 └─ services/api.ts       — streamRun() + the plain REST calls
```

**Why `streamRun()` hand-parses the SSE body instead of using the browser's
native `EventSource`:** `EventSource` only supports `GET`. `POST /run`
needs a JSON body (the goal), so `api.ts` reads the streamed
`fetch()` response body directly and parses `event:`/`data:` frames itself
— the standard workaround for POST+SSE. See `api.ts`'s own top-of-file note.

---

## Known scope limits (by design, not bugs)

Stated here rather than silently omitted:

- **Ollama as primary planner is unreliable** for complex multi-step tasks
  — works fine as an outage fallback (its actual job), not recommended as
  a primary driver.
- **Interactive programs can't be "run and confirmed."** `code_execution`'s
  subprocess never wires up stdin — a game needing live `input()` can be
  *written* (`file_system`) but not played and verified by the agent.
- **`code_execution` only has Python's standard library** — no pygame,
  numpy, requests, matplotlib, etc. A goal needing one of those fails
  fast with a clear message rather than a raw `ModuleNotFoundError`.
- **A hard multi-step debugging task can still exhaust all 3 retry
  attempts** — the retry loop is real self-correction, not guaranteed
  reliability on a genuinely hard bug.
