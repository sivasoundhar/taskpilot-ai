"""Does the planner turn a goal into a correct, tool-assigned plan?

Uses a fake LLM (duck-typing `with_structured_output` / `invoke`) so these
run fast, deterministically, and without a GROQ_API_KEY. Real plan *quality*
against a live Groq model is a manual check (needs an API key).
"""
from __future__ import annotations

import pytest

from src.agent.planner import IntentCheck, PlanningError, PlanOutput, build_planner_graph, check_intent, plan_goal
from src.models import PlannedStep, TaskStatus, ToolName


class _FakeStructuredLLM:
    """Stands in for what `llm.with_structured_output(PlanOutput)` returns."""

    def __init__(self, output: PlanOutput | Exception) -> None:
        self._output = output

    def invoke(self, _messages):
        if isinstance(self._output, Exception):
            raise self._output
        return self._output


class _FakeLLM:
    """Stands in for the ChatGroq instance — only `with_structured_output` is used."""

    def __init__(self, steps: list[PlannedStep] | Exception) -> None:
        output = steps if isinstance(steps, Exception) else PlanOutput(steps=steps)
        self._structured = _FakeStructuredLLM(output)

    def with_structured_output(self, _schema):
        return self._structured


def _steps(*tools: ToolName) -> list[PlannedStep]:
    return [
        PlannedStep(step_number=i + 1, tool=tool, description=f"do {tool.value}")
        for i, tool in enumerate(tools)
    ]


def test_multi_step_goal_returns_correct_step_breakdown():
    fake = _FakeLLM(_steps(ToolName.WEB_SEARCH, ToolName.CODE_EXECUTION, ToolName.FILE_SYSTEM))
    result = plan_goal("search AI news, analyze trends, save a report", llm=fake)
    assert len(result) == 3
    assert [s.tool for s in result] == [
        ToolName.WEB_SEARCH,
        ToolName.CODE_EXECUTION,
        ToolName.FILE_SYSTEM,
    ]


def test_single_step_goal_returns_one_step():
    fake = _FakeLLM(_steps(ToolName.CODE_EXECUTION))
    result = plan_goal("calculate 12 * 7", llm=fake)
    assert len(result) == 1
    assert result[0].tool == ToolName.CODE_EXECUTION


def test_tool_assignment_is_restricted_to_the_fixed_tool_set():
    """Pydantic enforces the 3-tools-only constraint at the schema level."""
    with pytest.raises(ValueError):
        PlannedStep(step_number=1, tool="database_query", description="not a real tool")


def test_step_numbers_are_renumbered_sequentially():
    """Don't trust the LLM's step_number field — renumber 1..n so the
    executor can safely index into the plan later."""
    fake = _FakeLLM(
        [
            PlannedStep(step_number=5, tool=ToolName.WEB_SEARCH, description="search"),
            PlannedStep(step_number=9, tool=ToolName.FILE_SYSTEM, description="save"),
        ]
    )
    result = plan_goal("search then save", llm=fake)
    assert [s.step_number for s in result] == [1, 2]


def test_empty_plan_raises_planning_error():
    fake = _FakeLLM([])
    with pytest.raises(PlanningError):
        plan_goal("do something impossible to plan", llm=fake)


def test_llm_failure_raises_planning_error_not_a_raw_exception():
    fake = _FakeLLM(ConnectionError("groq unreachable"))
    with pytest.raises(PlanningError):
        plan_goal("search something", llm=fake)


# ---------------------------------------------------------------------------
# _validate_plan() (via plan_goal): fail fast on an out-of-order plan.
# Found live: "save rate.json, convert, save conversion.txt" planned a
# read of conversion.txt at step 4, with the matching write not until
# step 6 -- each step was individually routed correctly, the plan's own
# ordering was just wrong.
# ---------------------------------------------------------------------------


def test_plan_goal_raises_when_a_read_references_a_file_nothing_creates_first(tmp_path, monkeypatch):
    # Isolated sandbox dir -- must NOT be the real workspace/, which can
    # (and did, found live) already have a leftover conversion.txt from
    # earlier real runs, silently passing this test for the wrong reason
    # (the "already exists" allowance, not the ordering check this test
    # actually targets).
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "files_sandbox_dir", str(tmp_path), raising=False)

    steps = [
        PlannedStep(
            step_number=1,
            tool=ToolName.FILE_SYSTEM,
            file_action="read",
            description="Read the conversion results from conversion.txt",
        ),
        PlannedStep(
            step_number=2,
            tool=ToolName.FILE_SYSTEM,
            file_action="write",
            description="Save the conversion results to conversion.txt",
        ),
    ]
    fake = _FakeLLM(steps)
    with pytest.raises(PlanningError, match="conversion.txt"):
        plan_goal("convert currency and save the results", llm=fake)


def test_plan_goal_allows_a_read_after_an_earlier_step_mentions_the_file():
    """Permissive on purpose: an earlier step mentioning the filename is
    enough, even if it's not a file_system write -- code_execution can
    write files directly too (it shares file_system's sandbox), and this check has no way to know
    what a not-yet-generated snippet will actually do."""
    steps = [
        PlannedStep(
            step_number=1,
            tool=ToolName.CODE_EXECUTION,
            description="Convert USD to INR and save the table to conversion.txt",
        ),
        PlannedStep(
            step_number=2,
            tool=ToolName.FILE_SYSTEM,
            file_action="read",
            description="Read conversion.txt to confirm it was saved",
        ),
    ]
    fake = _FakeLLM(steps)
    result = plan_goal("convert currency and save the results", llm=fake)
    assert len(result) == 2  # did not raise


def test_plan_goal_allows_reading_a_file_that_already_exists_on_disk(tmp_path, monkeypatch):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "files_sandbox_dir", str(tmp_path), raising=False)
    (tmp_path / "existing.txt").write_text("already here", encoding="utf-8")

    steps = [
        PlannedStep(
            step_number=1,
            tool=ToolName.FILE_SYSTEM,
            file_action="read",
            description="Read existing.txt and summarize it",
        ),
    ]
    fake = _FakeLLM(steps)
    result = plan_goal("summarize existing.txt", llm=fake)
    assert len(result) == 1  # did not raise -- the file is genuinely already there


def test_planner_graph_populates_state():
    """End-to-end through the LangGraph node, not just the bare function."""
    fake = _FakeLLM(_steps(ToolName.WEB_SEARCH, ToolName.FILE_SYSTEM))
    graph = build_planner_graph(llm=fake)
    final_state = graph.invoke({"goal": "search and save"})
    assert len(final_state["plan"]) == 2
    assert final_state["current_step"] == 0
    assert final_state["results"] == []
    assert final_state["status"] == TaskStatus.PLANNED


# ---------------------------------------------------------------------------
# check_intent(): is this an actionable task, or just chat? Added after a
# real bug -- "hi" used to be forced through plan_goal() and come back as
# "search the web for greeting message examples".
# ---------------------------------------------------------------------------


class _FakeIntentLLM:
    """Stands in for what `llm.with_structured_output(IntentCheck)` returns."""

    def __init__(self, output: IntentCheck | Exception) -> None:
        self._output = output

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        if isinstance(self._output, Exception):
            raise self._output
        return self._output


def test_check_intent_detects_a_greeting_as_non_task():
    fake = _FakeIntentLLM(IntentCheck(is_task=False, reply="Hey! What can I help with?"))
    result = check_intent("hi", llm=fake)
    assert result.is_task is False
    assert result.reply == "Hey! What can I help with?"


def test_check_intent_detects_an_actionable_task():
    fake = _FakeIntentLLM(IntentCheck(is_task=True, reply=""))
    result = check_intent("Calculate 12 * 7", llm=fake)
    assert result.is_task is True


def test_check_intent_falls_back_to_task_on_llm_failure():
    """A broken intent check must never block a real task -- worst case
    it degrades to the pre-existing always-plan behavior."""
    fake = _FakeIntentLLM(ConnectionError("groq unreachable"))
    result = check_intent("search something", llm=fake)
    assert result.is_task is True
