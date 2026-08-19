"""SSE event formatting helpers.

Turns what run_agent() yields (StepUpdate, TaskResult, ChatMessage — see
src/models.py) into the dict shape sse-starlette's EventSourceResponse
expects: {"event": <name>, "data": <string>}. Kept separate from
main.py so the endpoint handler stays about orchestration (which
generator to drive, when to stop) rather than serialization.

Event names a client listens for: "step" (one per step's progress),
"result" (exactly one, last, for an actual task), "chat" (exactly one,
instead of any step/result, when the input wasn't an actionable task —
see planner.check_intent), "error" (only if planning itself fails before
any step runs — see main.py's POST /run).
"""
from __future__ import annotations

from src.models import ChatMessage, StepUpdate, TaskResult


def step_event(update: StepUpdate) -> dict:
    return {"event": "step", "data": update.model_dump_json()}


def result_event(result: TaskResult) -> dict:
    return {"event": "result", "data": result.model_dump_json()}


def chat_event(message: ChatMessage) -> dict:
    return {"event": "chat", "data": message.model_dump_json()}


def error_event(message: str) -> dict:
    return {"event": "error", "data": message}
