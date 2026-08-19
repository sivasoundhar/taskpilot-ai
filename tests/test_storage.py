"""Run history storage (src/storage.py).

Each test gets an isolated throwaway DB file via the autouse
_isolate_history_db fixture in conftest.py -- never touches real dev data.
"""
from __future__ import annotations

from src import storage
from src.models import TaskResult


def _result(goal: str = "search AI news, save a report", success: bool = True) -> TaskResult:
    return TaskResult(
        goal=goal,
        success=success,
        summary="Completed 2/2 steps successfully." if success else "Stopped after 1/2 steps due to a failure.",
        steps_completed=2 if success else 1,
        steps_total=2,
    )


def test_save_and_list_a_run():
    storage.save_run(
        _result(),
        [
            {"tool": "web_search", "status": "done", "message": "Found 8 articles"},
            {"tool": "file_system", "status": "done", "message": "Saved to output.txt"},
        ],
    )

    runs = storage.list_runs()

    assert len(runs) == 1
    assert runs[0]["goal"] == "search AI news, save a report"
    assert runs[0]["success"] is True
    assert runs[0]["steps_completed"] == 2
    assert len(runs[0]["steps"]) == 2
    assert runs[0]["steps"][0]["tool"] == "web_search"


def test_list_runs_returns_most_recent_first():
    storage.save_run(_result("first goal"), [])
    storage.save_run(_result("second goal"), [])
    storage.save_run(_result("third goal"), [])

    runs = storage.list_runs()

    assert [r["goal"] for r in runs] == ["third goal", "second goal", "first goal"]


def test_list_runs_respects_limit():
    for i in range(5):
        storage.save_run(_result(f"goal {i}"), [])

    runs = storage.list_runs(limit=2)

    assert len(runs) == 2


def test_failed_run_is_recorded_as_such():
    storage.save_run(_result("a goal that failed", success=False), [])

    runs = storage.list_runs()

    assert runs[0]["success"] is False


def test_list_runs_on_an_empty_db_returns_empty_list():
    assert storage.list_runs() == []
