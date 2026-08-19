"""The complete agent loop: goal -> plan -> execute -> result, as one LangGraph graph.

Composes planner.py's planning node with executor.py's execution loop
(plan -> execute -> END). Kept in its own file rather than folded into
planner.py or executor.py, since "wire the two into one graph" is a
distinct concern from either — planner.py stays testable standalone,
executor.py stays focused on running a plan once one exists, and this
file is just the composition.

Two ways to drive a task, both built on the same execute_node:
  - `build_agent_graph()` — a plain LangGraph graph. `.invoke(...)` runs
    plan + execute to completion and returns final AgentState. No live
    step-by-step events — good for tests and non-streaming callers.
  - `run_agent(goal, ...)` — an async generator. First checks whether
    `goal` is even an actionable task (planner.check_intent) — if not
    (a greeting, small talk), yields a single ChatMessage and stops,
    rather than forcing a tool plan out of "hi". Otherwise plans, then
    yields each StepUpdate from executor.run_plan() as it happens,
    followed by one final TaskResult. This is what the SSE endpoint
    streams from directly.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, StateGraph

from src.agent.executor import CallTool, run_plan
from src.agent.planner import check_intent, plan_goal
from src.agent.state import AgentState
from src.models import ChatMessage, StepUpdate, TaskResult, TaskStatus


def build_agent_graph(
    llm: BaseChatModel | None = None,
    *,
    code_gen_llm: BaseChatModel | None = None,
    web_search_call_tool: CallTool | None = None,
    file_system_call_tool: CallTool | None = None,
):
    """LangGraph wrapper: state["goal"] -> plan -> execute -> final AgentState.

    All the injectable overrides (llm for planning, code_gen_llm/the two
    call_tool overrides for execution) pass straight through to
    planner.plan_goal / executor.run_plan — same fake-injection pattern
    used throughout this project's tests, now available end to end.
    """

    def plan_node(state: AgentState) -> AgentState:
        plan = plan_goal(state["goal"], llm=llm)
        return {"plan": plan, "current_step": 0, "results": [], "status": TaskStatus.PLANNED}

    async def execute_node(state: AgentState) -> AgentState:
        # run_plan mutates its argument in place; start from a shallow copy
        # of the merged graph state (which already has `plan` from
        # plan_node) so what's returned here is exactly what changed.
        working: AgentState = dict(state)  # type: ignore[assignment]
        async for _ in run_plan(
            working,
            code_gen_llm=code_gen_llm,
            web_search_call_tool=web_search_call_tool,
            file_system_call_tool=file_system_call_tool,
        ):
            pass  # side effects land in `working`; the graph doesn't stream (run_agent below does)
        return working

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("execute", execute_node)
    graph.set_entry_point("plan")
    graph.add_edge("plan", "execute")
    graph.add_edge("execute", END)
    return graph.compile()


async def run_agent(
    goal: str,
    *,
    llm: BaseChatModel | None = None,
    code_gen_llm: BaseChatModel | None = None,
    web_search_call_tool: CallTool | None = None,
    file_system_call_tool: CallTool | None = None,
) -> AsyncIterator[StepUpdate | TaskResult | ChatMessage]:
    """Yield a ChatMessage for non-task input, or a StepUpdate per step
    as execution happens followed by one final TaskResult for a real task.

    The streaming counterpart to build_agent_graph(): checks intent and
    plans synchronously (neither is a per-step event, just preconditions),
    then drives run_plan() and forwards its events, finishing with a
    TaskResult built from the final state. Consumers (the SSE endpoint)
    distinguish the three with isinstance() — the product needs both
    "stream step-updates" and "stream the final result" over the same
    connection; ChatMessage is the non-task escape hatch found necessary
    while live-testing the UI end to end.
    """
    intent = check_intent(goal, llm=llm)
    if not intent.is_task:
        yield ChatMessage(message=intent.reply or "Hi! Give me a task and I'll get to work.")
        return

    plan = plan_goal(goal, llm=llm)
    state: AgentState = {
        "goal": goal,
        "plan": plan,
        "current_step": 0,
        "results": [],
        "status": TaskStatus.PLANNED,
    }
    async for update in run_plan(
        state,
        code_gen_llm=code_gen_llm,
        web_search_call_tool=web_search_call_tool,
        file_system_call_tool=file_system_call_tool,
    ):
        yield update

    yield _build_task_result(state)


def _build_task_result(state: AgentState) -> TaskResult:
    results = state.get("results") or []
    plan = state.get("plan") or []
    success = state.get("status") == TaskStatus.DONE
    summary = (
        f"Completed {len(results)}/{len(plan)} steps successfully."
        if success
        else f"Stopped after {len(results)}/{len(plan)} steps due to a failure."
    )
    return TaskResult(
        goal=state.get("goal", ""),
        success=success,
        summary=summary,
        steps_completed=sum(1 for r in results if r.success),
        steps_total=len(plan),
    )
