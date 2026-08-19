"""The SSE streaming endpoint (POST /run).

Monkeypatches src.main.run_agent with a fake async generator, so these
test event formatting/ordering/delivery deterministically without a real
LLM or tool call — mirrors the fake-injection pattern used throughout
this project. One live test (real Groq + real tools) closes the loop,
skipped automatically if no GROQ_API_KEY is configured.
"""
from __future__ import annotations

import pytest

from src.config import get_settings
from src.models import ChatMessage, StepStatus, StepUpdate, TaskResult, ToolName


def _parse_sse_events(raw_text: str) -> list[dict]:
    """Minimal SSE parser, just enough to verify what the endpoint sent on
    the wire. sse-starlette uses CRLF ("\\r\\n") line endings per the SSE
    spec, not bare "\\n" -- normalize before splitting."""
    normalized = raw_text.replace("\r\n", "\n")
    events = []
    for block in normalized.strip().split("\n\n"):
        if not block.strip():
            continue
        event_type, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        if event_type is not None:
            events.append({"event": event_type, "data": data})
    return events


async def _fake_run_agent_success(goal, **_kwargs):
    yield StepUpdate(
        step_number=1, tool=ToolName.WEB_SEARCH, status=StepStatus.RUNNING, message="Running web_search..."
    )
    yield StepUpdate(
        step_number=1,
        tool=ToolName.WEB_SEARCH,
        status=StepStatus.DONE,
        message="Found 3 articles",
        result="Found 3 articles",
    )
    yield TaskResult(goal=goal, success=True, summary="Completed 1/1 steps successfully.", steps_completed=1, steps_total=1)


async def _fake_run_agent_planning_fails(_goal, **_kwargs):
    from src.agent.planner import PlanningError

    raise PlanningError("could not reach the LLM")
    yield  # pragma: no cover - makes this an async generator; never reached


async def _fake_run_agent_non_task(_goal, **_kwargs):
    yield ChatMessage(message="Hey! What can I help you with?")


def test_run_endpoint_streams_step_events_then_a_result_event(monkeypatch, client):
    monkeypatch.setattr("src.main.run_agent", _fake_run_agent_success)

    response = client.post("/run", json={"goal": "search AI news"})

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert [e["event"] for e in events] == ["step", "step", "result"]


def test_run_endpoint_step_events_have_running_then_done_status(monkeypatch, client):
    monkeypatch.setattr("src.main.run_agent", _fake_run_agent_success)

    response = client.post("/run", json={"goal": "search AI news"})

    events = _parse_sse_events(response.text)
    step_events = [e for e in events if e["event"] == "step"]
    assert '"status":"running"' in step_events[0]["data"]
    assert '"status":"done"' in step_events[1]["data"]


def test_run_endpoint_result_event_is_last_and_reports_success(monkeypatch, client):
    monkeypatch.setattr("src.main.run_agent", _fake_run_agent_success)

    response = client.post("/run", json={"goal": "search AI news"})

    events = _parse_sse_events(response.text)
    assert events[-1]["event"] == "result"
    assert '"success":true' in events[-1]["data"]


def test_run_endpoint_reports_a_planning_failure_as_an_error_event(monkeypatch, client):
    monkeypatch.setattr("src.main.run_agent", _fake_run_agent_planning_fails)

    response = client.post("/run", json={"goal": "an impossible goal"})

    assert response.status_code == 200  # SSE: the error is a stream event, not an HTTP error
    events = _parse_sse_events(response.text)
    assert events == [{"event": "error", "data": "could not reach the LLM"}]


def test_run_endpoint_rejects_an_empty_goal(client):
    response = client.post("/run", json={"goal": ""})
    assert response.status_code == 422  # Pydantic: TaskRequest.goal has min_length=1


def test_run_endpoint_streams_a_chat_event_for_non_task_input(monkeypatch, client):
    """Regression test for the exact bug found: "hi" used to be forced
    through the full step/result pipeline instead of getting a plain reply."""
    monkeypatch.setattr("src.main.run_agent", _fake_run_agent_non_task)

    response = client.post("/run", json={"goal": "hi"})

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert events == [{"event": "chat", "data": '{"message":"Hey! What can I help you with?"}'}]


@pytest.mark.skipif(not get_settings().groq_api_key, reason="requires GROQ_API_KEY")
def test_run_endpoint_live_end_to_end(client):
    """Real Groq plans, real tools execute, over the real HTTP endpoint."""
    response = client.post(
        "/run", json={"goal": "Calculate 7 times 8 and save the result to a file"}
    )

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert events[-1]["event"] == "result"
    assert '"success":true' in events[-1]["data"]
    # At least one step event per tool actually used.
    assert any(e["event"] == "step" for e in events)
