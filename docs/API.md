# API Reference

FastAPI backend (`src/main.py`), served at `http://127.0.0.1:8000` in dev.
The frontend talks to it via relative paths, proxied by Vite
(`frontend/vite.config.ts`) — every route below has a proxy entry there.

For *why* things are shaped this way (why `POST /run` streams instead of
returning JSON, why the SSE client is hand-rolled), see `docs/ARCHITECTURE.md`.

---

## `GET /health`

Liveness check.

**Response `200`:**
```json
{ "status": "ok", "env": "development" }
```

---

## `POST /run`

The actual product: runs the agent on a goal, streaming its progress live
over Server-Sent Events. This is what the frontend's live step-card UI
consumes.

**Request body:**
```json
{ "goal": "Search the latest AI news, summarize the top 3, save to report.txt" }
```
`goal` — string, required, non-empty (`400` on empty).

**Response:** `text/event-stream`. The connection stays open and emits a
sequence of named SSE events, always ending in exactly one terminal event
(`result`, `chat`, or `error` — never a silent drop).

### Event: `step`

Emitted once per step's `RUNNING` start and once more for its `DONE`/`FAILED`
finish. A `code_execution` step that retries emits an extra `RUNNING`/`FAILED`
pair per attempt (up to 3 attempts total) — the retry is visible live, not
hidden. This is the event the frontend's `StepCard` component renders.

```json
{
  "step_number": 1,
  "tool": "web_search",
  "status": "running",
  "message": "Running web_search: search for latest AI news",
  "result": null,
  "content": null
}
```
```json
{
  "step_number": 2,
  "tool": "file_system",
  "status": "done",
  "message": "Saved to report.txt",
  "result": "Saved to report.txt",
  "content": "# AI News Summary\n\n1. ..."
}
```

| Field | Type | Notes |
|---|---|---|
| `step_number` | int | 1-indexed, matches the plan's own numbering |
| `tool` | `"web_search" \| "file_system" \| "code_execution"` | |
| `status` | `"running" \| "done" \| "failed"` | (`"pending"` exists in the schema but is never emitted over the wire) |
| `message` | string | Human-readable, truncated to 200 chars for `done`/`failed` |
| `result` | string \| null | Full tool output, once the step finishes |
| `content` | string \| null | Only set for a successful `file_system` step — the actual file content, for UI preview |

### Event: `result`

Exactly one, last, only for an actual task (not chit-chat).

```json
{
  "goal": "Search the latest AI news, summarize the top 3, save to report.txt",
  "success": true,
  "summary": "Completed 3/3 steps successfully.",
  "steps_completed": 3,
  "steps_total": 3
}
```

### Event: `chat`

Sent **instead of** any `step`/`result` events when `goal` isn't an
actionable task (a greeting, small talk — see `planner.check_intent()`).

```json
{ "message": "Hi! Give me a task and I'll get to work." }
```

### Event: `error`

Sent **instead of** any `step`/`result` events if planning itself fails
before a single step runs (e.g. the LLM call errors, or the plan fails
`_validate_plan()`'s ordering check). `data` is a plain string, not JSON.

```
event: error
data: Planning failed for goal '...': ...
```

### Example: reading the stream with `curl`

```bash
curl -N -X POST http://127.0.0.1:8000/run \
  -H "Content-Type: application/json" \
  -d '{"goal": "Calculate compound interest on 5000 at 4% for 6 years and save the result"}'
```

---

## `GET /history`

Past completed runs, most recent first — backs the frontend's History page.

**Query params:** `limit` (int, default 50)

**Response `200`:**
```json
[
  {
    "goal": "Calculate compound interest on 5000 at 4% for 6 years and save the result",
    "success": true,
    "summary": "Completed 2/2 steps successfully.",
    "steps": [
      { "tool": "code_execution", "status": "done", "message": "6326.60" },
      { "tool": "file_system", "status": "done", "message": "Saved to result.txt" }
    ]
  }
]
```
(Exact row shape comes from `src/storage.py::save_run` — a goal, its final
result, and the per-step summary built up while streaming.)

---

## `GET /settings`

Read-only status view of current config, plus the live-editable knobs below.
Secrets are never included — only whether they're configured.

**Response `200`:**
```json
{
  "app_env": "development",
  "log_level": "INFO",
  "groq_model": "openai/gpt-oss-120b",
  "ollama_model": "llama3.2",
  "files_sandbox_dir": "./workspace",
  "cors_origins": ["http://localhost:5173", "http://localhost:3000"],
  "groq_api_key_configured": true,
  "tavily_api_key_configured": false,
  "ollama_reachable": false,
  "llm_provider_preference": "groq_first",
  "web_search_max_results": 5
}
```
`ollama_reachable` is a **live** check (a real request to Ollama's
`/api/tags`), not just "is it configured" — Ollama needs no key to be "on"
the way Groq/Tavily do, so reachability is the only meaningful status.

---

## `PATCH /settings`

Updates whichever of the live-tunable knobs are sent — every field
optional, only what actually changed needs to be included. Returns the
same shape as `GET /settings`.

**Request body (all fields optional):**
```json
{
  "llm_provider_preference": "ollama_first",
  "log_level": "DEBUG",
  "web_search_max_results": 8
}
```

| Field | Valid values |
|---|---|
| `llm_provider_preference` | `"groq_first"` \| `"ollama_first"` |
| `log_level` | `"DEBUG"` \| `"INFO"` \| `"WARNING"` \| `"ERROR"` |
| `web_search_max_results` | integer, 1–20 |

**Response `400`** on an unknown/out-of-range value:
```json
{ "detail": "Unknown log level: 'VERBOSE'" }
```

These settings are **in-memory only** (`src/runtime_settings.py`) — they
reset to `config.py`'s `.env` defaults on every backend restart, by design.
