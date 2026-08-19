"""The full agent loop (src/agent/graph.py, src/agent/executor.py).

Fakes every external dependency (planner LLM, code-gen LLM, both tools'
MCP call_tool) so the whole plan -> execute -> chain -> result path is
tested deterministically, without a single real subprocess or network
call. One live end-to-end test (real Groq, real tools) closes the loop --
skipped automatically if no GROQ_API_KEY is configured.
"""
from __future__ import annotations

import pytest

from src.agent.executor import run_plan
from src.agent.graph import build_agent_graph, run_agent
from src.agent.planner import IntentCheck, PlanOutput
from src.config import get_settings
from src.models import ChatMessage, PlannedStep, StepStatus, StepUpdate, TaskResult, TaskStatus, ToolName


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakePlannerLLM:
    """Stands in for the ChatGroq instance passed as planner `llm`.

    Schema-aware: run_agent() calls check_intent() (requesting an
    IntentCheck) before plan_goal() (requesting a PlanOutput), both
    against this same fake -- it has to return the right shape for
    whichever one asked. Defaults to is_task=True so every test that
    predates the intent-check feature keeps working unchanged; only the
    tests specifically exercising the non-task/chat path override it.
    """

    def __init__(self, steps: list[PlannedStep], *, is_task: bool = True, chat_reply: str = "") -> None:
        self._plan_output = PlanOutput(steps=steps)
        self._intent = IntentCheck(is_task=is_task, reply=chat_reply)
        self._requested_schema: type | None = None

    def with_structured_output(self, schema):
        self._requested_schema = schema
        return self

    def invoke(self, _messages):
        if self._requested_schema is IntentCheck:
            return self._intent
        return self._plan_output


class _FakeCodeGenLLM:
    def __init__(self, code: str) -> None:
        self._code = code

    async def ainvoke(self, _messages):
        return _FakeResponse(self._code)


class _SequentialCodeGenLLM:
    """Returns a different response on each successive call -- simulates
    "first attempt's code fails, the retry's code succeeds" for the
    code_execution retry loop. Also records every call's messages
    so a test can confirm the previous failure's error actually reached
    the next attempt's prompt, not just that a retry happened at all."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[list] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return _FakeResponse(self._responses.pop(0))


async def _fake_web_search_call_tool(_command, _args, tool_name, _tool_args):
    assert tool_name == "search"
    return "1. Fake Article\n   https://example.com\n   A fake but realistic snippet."


async def _fake_file_system_call_tool(_command, _args, tool_name, tool_args):
    assert tool_name == "write_file"
    return f"Successfully wrote to {tool_args['path']}"


def _step(number: int, tool: ToolName, description: str = "do the thing") -> PlannedStep:
    return PlannedStep(step_number=number, tool=tool, description=description)


# ---------------------------------------------------------------------------
# run_plan(): routing order + chaining
# ---------------------------------------------------------------------------


async def test_search_then_save_uses_web_search_then_file_system_in_order():
    steps = [_step(1, ToolName.WEB_SEARCH, "search for X"), _step(2, ToolName.FILE_SYSTEM, "save findings")]
    state = {"goal": "search X, save to file", "plan": steps}

    updates = [
        u
        async for u in run_plan(
            state,
            code_gen_llm=_FakeCodeGenLLM("Fake Article\nhttps://example.com"),
            web_search_call_tool=_fake_web_search_call_tool,
            file_system_call_tool=_fake_file_system_call_tool,
        )
    ]

    tools_in_order = [u.tool for u in updates if u.status == StepStatus.DONE]
    assert tools_in_order == [ToolName.WEB_SEARCH, ToolName.FILE_SYSTEM]
    assert state["status"] == TaskStatus.DONE
    assert len(state["results"]) == 2
    # Chaining: file_system should have saved web_search's own output.
    assert "Fake Article" in state["results"][0].output


async def test_calculate_then_save_uses_code_execution_then_file_system():
    steps = [_step(1, ToolName.CODE_EXECUTION, "calculate X"), _step(2, ToolName.FILE_SYSTEM, "save result")]
    state = {"goal": "calculate X, save result", "plan": steps}

    updates = [
        u
        async for u in run_plan(
            state,
            code_gen_llm=_FakeCodeGenLLM("print(42)"),
            file_system_call_tool=_fake_file_system_call_tool,
        )
    ]

    tools_in_order = [u.tool for u in updates if u.status == StepStatus.DONE]
    assert tools_in_order == [ToolName.CODE_EXECUTION, ToolName.FILE_SYSTEM]
    assert state["results"][0].output.strip() == "42"


async def test_three_tool_task_uses_all_three_in_order():
    steps = [
        _step(1, ToolName.WEB_SEARCH, "search"),
        _step(2, ToolName.CODE_EXECUTION, "analyze"),
        _step(3, ToolName.FILE_SYSTEM, "save"),
    ]
    state = {"goal": "search, analyze, save", "plan": steps}

    updates = [
        u
        async for u in run_plan(
            state,
            code_gen_llm=_FakeCodeGenLLM("print('analysis done')"),
            web_search_call_tool=_fake_web_search_call_tool,
            file_system_call_tool=_fake_file_system_call_tool,
        )
    ]

    tools_in_order = [u.tool for u in updates if u.status == StepStatus.DONE]
    assert tools_in_order == [ToolName.WEB_SEARCH, ToolName.CODE_EXECUTION, ToolName.FILE_SYSTEM]
    assert state["status"] == TaskStatus.DONE
    assert len(state["results"]) == 3


async def test_each_step_emits_a_running_then_done_update():
    """The live step-card UI needs a RUNNING event
    before a DONE one for every step, not just a final summary."""
    steps = [_step(1, ToolName.WEB_SEARCH, "search")]
    state = {"goal": "search", "plan": steps}

    updates = [u async for u in run_plan(state, web_search_call_tool=_fake_web_search_call_tool)]

    assert [u.status for u in updates] == [StepStatus.RUNNING, StepStatus.DONE]
    assert all(u.step_number == 1 for u in updates)


async def test_tool_failure_is_handled_gracefully_stops_and_reports():
    """A failing step stops the plan (a chained step shouldn't run on bad
    data) but never raises -- the caller gets a clean FAILED status."""

    async def _failing_web_search_call_tool(_command, _args, _tool_name, _tool_args):
        return ""  # empty results -> WebSearchError inside web_search.run

    steps = [_step(1, ToolName.WEB_SEARCH, "search"), _step(2, ToolName.FILE_SYSTEM, "save")]
    state = {"goal": "search then save", "plan": steps}

    updates = [u async for u in run_plan(state, web_search_call_tool=_failing_web_search_call_tool)]

    assert state["status"] == TaskStatus.FAILED
    assert len(state["results"]) == 1  # step 2 never ran
    assert updates[-1].status == StepStatus.FAILED
    assert updates[-1].step_number == 1


# ---------------------------------------------------------------------------
# Bounded retry loop for CODE_EXECUTION steps only -- if the script
# fails, fix it and rerun until it succeeds, after
# hitting the "code_execution can't see files file_system wrote" gap 3
# times. Scoped to CODE_EXECUTION deliberately: a failed web_search/
# file_system step is usually an infra problem, not something
# regenerating code around it fixes.
# ---------------------------------------------------------------------------


async def test_code_execution_step_retries_and_succeeds_on_second_attempt():
    steps = [_step(1, ToolName.CODE_EXECUTION, "divide then fix it")]
    state = {"goal": "divide then fix it", "plan": steps}
    fake_llm = _SequentialCodeGenLLM(["1 / 0", "print(1 / 2)"])

    updates = [u async for u in run_plan(state, code_gen_llm=fake_llm)]

    assert state["status"] == TaskStatus.DONE
    assert state["results"][0].success is True
    assert state["results"][0].output.strip() == "0.5"
    # Visible live, not hidden: RUNNING(1) -> FAILED(1, retrying) -> RUNNING(2) -> DONE
    assert [u.status for u in updates] == [
        StepStatus.RUNNING,
        StepStatus.FAILED,
        StepStatus.RUNNING,
        StepStatus.DONE,
    ]
    assert "attempt 1/3" in updates[0].message
    assert "attempt 2/3" in updates[2].message


async def test_code_execution_retry_feeds_the_previous_error_into_the_next_prompt():
    """Not just "a retry happened" -- the actual failure reason has to
    reach the regenerated code's prompt, or the retry is just a blind
    second guess."""
    steps = [_step(1, ToolName.CODE_EXECUTION, "divide then fix it")]
    state = {"goal": "divide then fix it", "plan": steps}
    fake_llm = _SequentialCodeGenLLM(["1 / 0", "print(1 / 2)"])

    async for _ in run_plan(state, code_gen_llm=fake_llm):
        pass

    assert len(fake_llm.calls) == 2
    second_attempt_prompt = str(fake_llm.calls[1])
    assert "ZeroDivisionError" in second_attempt_prompt


async def test_code_execution_step_gives_up_after_max_attempts():
    steps = [_step(1, ToolName.CODE_EXECUTION, "always fails")]
    state = {"goal": "always fails", "plan": steps}
    fake_llm = _SequentialCodeGenLLM(["1 / 0", "1 / 0", "1 / 0"])

    updates = [u async for u in run_plan(state, code_gen_llm=fake_llm)]

    assert state["status"] == TaskStatus.FAILED
    assert len(fake_llm.calls) == 3  # exhausted all 3 attempts, not more
    assert updates[-1].status == StepStatus.FAILED
    # .result is the untruncated text (.message is capped at 200 chars for
    # the short status line -- the full traceback here is longer than that).
    assert "ZeroDivisionError" in updates[-1].result


async def test_web_search_failure_does_not_retry():
    """Retry is scoped to CODE_EXECUTION only -- a failed web_search
    should still stop after one attempt, not silently retry 3x."""

    async def _failing_web_search_call_tool(_command, _args, _tool_name, _tool_args):
        return ""

    steps = [_step(1, ToolName.WEB_SEARCH, "search")]
    state = {"goal": "search", "plan": steps}

    updates = [u async for u in run_plan(state, web_search_call_tool=_failing_web_search_call_tool)]

    assert [u.status for u in updates] == [StepStatus.RUNNING, StepStatus.FAILED]
    assert "attempt" not in updates[0].message  # no retry framing for a single-attempt tool


# ---------------------------------------------------------------------------
# The full LangGraph (src/agent/graph.py): planner + executor as one graph
# ---------------------------------------------------------------------------


async def test_agent_graph_runs_plan_then_execute_end_to_end():
    planner_llm = _FakePlannerLLM(
        [_step(1, ToolName.WEB_SEARCH, "search"), _step(2, ToolName.FILE_SYSTEM, "save")]
    )
    graph = build_agent_graph(
        llm=planner_llm,
        code_gen_llm=_FakeCodeGenLLM("Fake Article\nhttps://example.com"),
        web_search_call_tool=_fake_web_search_call_tool,
        file_system_call_tool=_fake_file_system_call_tool,
    )

    final_state = await graph.ainvoke({"goal": "search X, save to file"})

    assert final_state["status"] == TaskStatus.DONE
    assert len(final_state["results"]) == 2
    assert final_state["results"][0].tool == ToolName.WEB_SEARCH
    assert final_state["results"][1].tool == ToolName.FILE_SYSTEM


async def test_run_agent_streams_updates_then_a_final_result():
    planner_llm = _FakePlannerLLM([_step(1, ToolName.CODE_EXECUTION, "calculate")])

    items = [
        u
        async for u in run_agent(
            "calculate something", llm=planner_llm, code_gen_llm=_FakeCodeGenLLM("print('done')")
        )
    ]

    step_updates = [i for i in items if isinstance(i, StepUpdate)]
    results = [i for i in items if isinstance(i, TaskResult)]

    assert [u.status for u in step_updates] == [StepStatus.RUNNING, StepStatus.DONE]
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].steps_completed == 1
    assert results[0].steps_total == 1
    # The TaskResult must be the very last item -- consumers (the SSE
    # endpoint) rely on step events always preceding the final result.
    assert items[-1] is results[0]


# ---------------------------------------------------------------------------
# Non-task input ("hi"): check_intent() should short-circuit before any
# planning/tool call happens, per the bug found live-testing the UI.
# ---------------------------------------------------------------------------


async def test_non_task_input_yields_only_a_chat_message():
    """A greeting must never reach plan_goal() -- if it did, the fake
    planner here (configured with 0 steps) would raise PlanningError on
    an empty plan, so this also proves planning was skipped entirely."""
    fake_llm = _FakePlannerLLM([], is_task=False, chat_reply="Hey! What can I help you with?")

    items = [u async for u in run_agent("hi", llm=fake_llm)]

    assert len(items) == 1
    assert isinstance(items[0], ChatMessage)
    assert items[0].message == "Hey! What can I help you with?"


async def test_non_task_input_never_calls_a_tool():
    """Defense in depth: even if check_intent's fallback text were empty,
    no web_search/file_system/code_execution call_tool should ever fire
    for chit-chat input."""

    async def _tool_call_that_must_never_happen(*_args, **_kwargs):
        raise AssertionError("a tool was called for non-task input")

    fake_llm = _FakePlannerLLM([], is_task=False)

    items = [
        u
        async for u in run_agent(
            "thanks!",
            llm=fake_llm,
            web_search_call_tool=_tool_call_that_must_never_happen,
            file_system_call_tool=_tool_call_that_must_never_happen,
        )
    ]

    assert len(items) == 1
    assert isinstance(items[0], ChatMessage)


# ---------------------------------------------------------------------------
# Live: real Groq plans, real tools execute. Skipped if no GROQ_API_KEY.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not get_settings().groq_api_key, reason="requires GROQ_API_KEY")
async def test_full_agent_live_end_to_end():
    """The actual demo-script task, run for real: search -> ... ->
    save. Proves the whole graph works live, not just against fakes.

    Doesn't assert an exact step count -- the planner may reasonably
    choose 2 steps (search, save) or 3 (search, verify/extract via code,
    save); both are valid plans for this goal. What matters: it starts
    with web_search, ends with file_system, and every step succeeds.
    """
    graph = build_agent_graph()
    final_state = await graph.ainvoke(
        {"goal": "Find Python's latest version and write a short note about it to a file"}
    )

    assert final_state["status"] == TaskStatus.DONE
    assert len(final_state["results"]) >= 2
    assert all(r.success for r in final_state["results"]), final_state["results"]
    assert final_state["results"][0].tool == ToolName.WEB_SEARCH
    assert final_state["results"][-1].tool == ToolName.FILE_SYSTEM


@pytest.mark.skipif(not get_settings().groq_api_key, reason="requires GROQ_API_KEY")
async def test_greeting_gets_a_chat_reply_not_a_forced_task_live():
    """Regression test for the exact bug found: "hi" used to be forced
    through plan_goal() and come back as "search the web for greeting
    message examples". Real Groq call, no fakes."""
    items = [u async for u in run_agent("hi")]

    assert len(items) == 1
    assert isinstance(items[0], ChatMessage)
    assert items[0].message  # non-empty, an actual reply
