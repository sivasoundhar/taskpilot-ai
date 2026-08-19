"""GET /history and GET /settings — plain JSON endpoints backing the
Tasks/History and Settings nav pages (previously decorative placeholders).
"""
from __future__ import annotations

from src import storage
from src.models import TaskResult


def test_history_is_empty_before_any_run(client):
    response = client.get("/history")
    assert response.status_code == 200
    assert response.json() == []


def test_history_returns_a_previously_saved_run(client):
    storage.save_run(
        TaskResult(
            goal="search AI news, save a report",
            success=True,
            summary="Completed 2/2 steps successfully.",
            steps_completed=2,
            steps_total=2,
        ),
        [{"tool": "web_search", "status": "done", "message": "Found 8 articles"}],
    )

    response = client.get("/history")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["goal"] == "search AI news, save a report"
    assert body[0]["success"] is True


def test_run_endpoint_persists_a_completed_task_to_history(monkeypatch, client):
    """POST /run for a real task should leave a trace in GET /history --
    the actual integration point, not just storage.py in isolation."""
    from src.models import StepStatus, StepUpdate, ToolName

    async def _fake_run_agent(goal, **_kwargs):
        yield StepUpdate(
            step_number=1, tool=ToolName.CODE_EXECUTION, status=StepStatus.RUNNING, message="Running..."
        )
        yield StepUpdate(
            step_number=1, tool=ToolName.CODE_EXECUTION, status=StepStatus.DONE, message="42", result="42"
        )
        yield TaskResult(goal=goal, success=True, summary="Completed 1/1 steps successfully.", steps_completed=1, steps_total=1)

    monkeypatch.setattr("src.main.run_agent", _fake_run_agent)

    client.post("/run", json={"goal": "calculate something"})
    response = client.get("/history")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["goal"] == "calculate something"
    assert body[0]["steps"] == [{"tool": "code_execution", "status": "done", "message": "42"}]


def test_run_endpoint_never_persists_chit_chat(monkeypatch, client):
    """A greeting (ChatMessage, no plan) must never show up in history --
    it was never a task."""

    async def _fake_run_agent_chat(_goal, **_kwargs):
        from src.models import ChatMessage

        yield ChatMessage(message="Hey! What can I help with?")

    monkeypatch.setattr("src.main.run_agent", _fake_run_agent_chat)

    client.post("/run", json={"goal": "hi"})
    response = client.get("/history")

    assert response.json() == []


def test_settings_endpoint_excludes_secret_values(client):
    response = client.get("/settings")

    assert response.status_code == 200
    body = response.json()
    assert "groq_api_key" not in body
    assert "tavily_api_key" not in body
    assert "groq_api_key_configured" in body
    assert "tavily_api_key_configured" in body
    assert "groq_model" in body
    # Ollama fallback status: ollama_reachable is a *live* check (is the
    # fallback actually usable right now), not just "is it configured" --
    # Ollama needs no key, unlike Groq/Tavily above. Only asserting the
    # field's shape here, not its value: whether Ollama happens to be
    # running isn't this test's concern (see test_llm_provider.py).
    assert "ollama_model" in body
    assert isinstance(body["ollama_reachable"], bool)
    assert body["llm_provider_preference"] == "groq_first"
    assert body["web_search_max_results"] == 5


def test_patch_settings_updates_and_echoes_the_new_value(client):
    response = client.patch("/settings", json={"web_search_max_results": 8})

    assert response.status_code == 200
    assert response.json()["web_search_max_results"] == 8

    # Confirms it's a real, persisted (process-lifetime) change, not just
    # echoed back once -- a fresh GET sees it too.
    assert client.get("/settings").json()["web_search_max_results"] == 8


def test_patch_settings_only_updates_the_fields_sent(client):
    client.patch("/settings", json={"llm_provider_preference": "ollama_first"})
    response = client.patch("/settings", json={"web_search_max_results": 3})

    body = response.json()
    assert body["web_search_max_results"] == 3
    assert body["llm_provider_preference"] == "ollama_first"  # untouched by the second PATCH


def test_patch_settings_rejects_an_invalid_value(client):
    """llm_provider_preference has no schema-level constraint (any string
    passes Pydantic) -- runtime_settings.update_runtime_settings() is what
    actually validates it, surfaced here as a 400, not a silent no-op."""
    response = client.patch("/settings", json={"llm_provider_preference": "bing_first"})
    assert response.status_code == 400


def test_patch_settings_rejects_an_out_of_range_value_at_the_schema_level(client):
    """web_search_max_results=999 never reaches update_runtime_settings()
    at all -- Pydantic's own ge/le on RuntimeSettingsUpdate rejects it
    first (422), a second line of defense on top of runtime_settings'
    own range check."""
    response = client.patch("/settings", json={"web_search_max_results": 999})
    assert response.status_code == 422
