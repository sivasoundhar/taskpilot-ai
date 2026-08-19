"""FastAPI entrypoint.

App scaffold + /health, plus POST /run, the agent's actual entrypoint --
streams the live step-by-step progress the frontend's step-card UI
renders, over SSE.
"""
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from src import storage
from src.agent import llm_provider
from src.agent.graph import run_agent
from src.agent.planner import PlanningError
from src.config import get_settings
from src.models import ChatMessage, RuntimeSettingsUpdate, StepStatus, StepUpdate, TaskRequest
from src.runtime_settings import get_runtime_settings, effective_log_level, update_runtime_settings
from src.utils.streaming import chat_event, error_event, result_event, step_event

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("taskpilot")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("TaskPilot AI starting up (env=%s)", settings.app_env)
    yield


app = FastAPI(
    title="TaskPilot AI",
    description="Autonomous agent that plans and executes multi-step tasks via MCP tools.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness check used by Docker/Render and the project's own smoke tests."""
    return {"status": "ok", "env": settings.app_env}


@app.post("/run")
async def run_task(request: TaskRequest) -> EventSourceResponse:
    """Streams the agent's progress for `request.goal` over SSE.

    Event stream: if `goal` isn't actually an actionable task (a
    greeting, small talk), a single "chat" event with a plain reply and
    nothing else. Otherwise: one "step" event per step's RUNNING then
    DONE/FAILED (the live step-card requirement — the UI renders
    these one by one as they arrive, not after the whole task finishes),
    then exactly one "result" event once the plan completes or stops. If
    planning itself fails before any step runs, a single "error" event
    is sent instead — the connection always ends with one terminal
    event, never a silent drop.
    """

    async def event_generator() -> AsyncIterator[dict]:
        # Built up as step events stream by, then persisted once the
        # final `result` arrives -- gives the History nav page something
        # to show. Never touches chit-chat (ChatMessage), only real
        # tasks. Kept out of run_agent()/graph.py on purpose so the agent
        # core stays side-effect-free for tests.
        step_summaries: list[dict] = []
        try:
            async for item in run_agent(request.goal):
                if isinstance(item, StepUpdate):
                    yield step_event(item)
                    if item.status != StepStatus.RUNNING:
                        step_summaries.append(
                            {"tool": item.tool.value, "status": item.status.value, "message": item.message}
                        )
                elif isinstance(item, ChatMessage):
                    yield chat_event(item)
                else:
                    storage.save_run(item, step_summaries)
                    yield result_event(item)
        except PlanningError as exc:
            logger.warning("Planning failed for goal %r: %s", request.goal, exc)
            yield error_event(str(exc))

    return EventSourceResponse(event_generator())


@app.get("/history")
async def history(limit: int = 50) -> list[dict]:
    """Past completed tasks. Backs the "History" nav page."""
    return storage.list_runs(limit=limit)


async def _settings_snapshot() -> dict:
    """Shared by GET and PATCH /settings so both always return the exact
    same shape -- a PATCH response is just "the new GET result", not a
    separately-maintained echo of what was sent."""
    runtime = get_runtime_settings()
    return {
        "app_env": settings.app_env,
        # Live value, not settings.log_level's startup default -- reflects
        # a runtime override from PATCH /settings if one was ever applied.
        "log_level": effective_log_level(),
        "groq_model": settings.groq_model,
        "ollama_model": settings.ollama_model,
        "files_sandbox_dir": settings.files_sandbox_dir,
        "cors_origins": settings.cors_origins_list,
        "groq_api_key_configured": bool(settings.groq_api_key),
        "tavily_api_key_configured": bool(settings.tavily_api_key),
        # ollama_reachable is a *live* check (llm_provider.is_ollama_reachable),
        # not just "is it configured" -- Ollama needs no key/config to be
        # "on" the way Groq/Tavily do, so the only meaningful status for it
        # is whether the fallback would actually work right now.
        "ollama_reachable": await llm_provider.is_ollama_reachable(),
        # The few knobs PATCH /settings can actually change, live, without
        # a restart -- see src/runtime_settings.py.
        "llm_provider_preference": runtime.llm_provider_preference,
        "web_search_max_results": runtime.web_search_max_results,
    }


@app.get("/settings")
async def settings_view() -> dict:
    """Read-only status view of current backend config (env, sandbox dir,
    CORS, whether API keys are configured) plus the live-editable knobs
    below -- see PATCH /settings. Secrets are never included, only whether
    they're configured, matching the project's "no API keys in code"
    principle extended to API responses too."""
    return await _settings_snapshot()


@app.patch("/settings")
async def update_settings(body: RuntimeSettingsUpdate) -> dict:
    """Applies whichever of the live-tunable knobs were sent (provider
    preference, log level, search result count) and returns the fresh
    settings view -- the actual production path for the Settings page's
    now-functional controls, not just a display of what config.py loaded
    at startup. A bad value (e.g. an unknown log level) is a 400, not a
    silent no-op or a 500."""
    try:
        update_runtime_settings(
            llm_provider_preference=body.llm_provider_preference,
            log_level=body.log_level,
            web_search_max_results=body.web_search_max_results,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _settings_snapshot()
