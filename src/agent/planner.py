"""LangGraph planning loop: goal -> ordered, tool-assigned steps.

A single-node graph. The LLM reads the user's goal and
breaks it into steps, each assigned exactly one of the 3 fixed tools
(web_search / file_system / code_execution). The executor
consumes `state["plan"]`; this file never runs a tool itself.

`plan_goal()` is kept separate from the LangGraph node on purpose so it
can be unit-tested with a fake LLM (no network / API key needed) — see
tests/test_planner.py.
"""
from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from src.agent import llm_provider
from src.agent.state import AgentState
from src.models import PlannedStep, TaskStatus, ToolName

logger = logging.getLogger("taskpilot.agent.planner")

SYSTEM_PROMPT = """You are the planning module of TaskPilot AI, an agent with exactly 3 tools:

- web_search: fetch live information from the internet
- file_system: read or write files
- code_execution: run Python code (math, data analysis, logic) in a
  sandboxed subprocess with ONLY Python's standard library available —
  no third-party or GUI/graphics packages (no pygame, no tkinter display,
  no matplotlib window, nothing needing a screen or an installed library
  beyond stdlib).

Given the user's goal, break it into an ordered list of steps. Each step
must use exactly one of the 3 tools above. Use the minimum number of
steps the goal actually needs — don't split it further than necessary,
and don't add a tool the goal doesn't call for.

If the goal asks to *write* code that plausibly needs a library
code_execution doesn't have (a game with pygame, a GUI app, anything
graphical), the ENTIRE plan must use file_system only for that code --
zero code_execution steps anywhere in the plan, not even ones framed as
"write the structure," "implement the next part," or "verify progress."
Every one of those will still fail the moment the code imports the
missing library, however the step is worded. The same applies to
anything needing live keyboard/mouse input during play (a playable
game) -- code_execution runs once, non-interactively, with no screen and
no input device attached, so it can never "run and confirm" that kind of
program either. Only plan a code_execution step when the code is
realistically expected to run non-interactively using stdlib alone
(math, data processing, simple logic, text output).

If the goal names a specific filename (e.g. "breakout.py", "report.csv"),
repeat that exact filename in every step's description that creates,
reads, or writes it -- do not paraphrase it into something generic like
"create a new file for the game." A step description with no filename in
it can't be routed to the right file later.

Respond with a JSON object matching the given schema: a "steps" array,
each item has step_number, tool (one of web_search/file_system/code_execution),
a short description of what that step will do, and file_action.

file_action only matters when tool is file_system — set it to "read" if
the step inspects/reads an existing file (e.g. "read snake.py and explain
it"), or "write" if it creates/saves/overwrites one (e.g. "save the
findings to report.txt"). Get this right: a "read" step routed as "write"
would overwrite the very file it was supposed to read. Leave file_action
null for web_search and code_execution steps.
"""


class PlanOutput(BaseModel):
    """Structured-output schema bound to the LLM.

    Wrapped in an object (not a bare list) because tool-calling / JSON
    mode structured output needs an object schema at the top level.
    """

    steps: list[PlannedStep]


class PlanningError(RuntimeError):
    """Raised when the LLM call fails or produces an unusable plan."""


_INTENT_SYSTEM_PROMPT = """You are TaskPilot AI's front door. Decide whether
the user's message describes an actionable task you should plan and execute
using tools (web search, file system, code execution) -- or whether it's
just conversational (a greeting, small talk, thanks, asking what you can do)
with no real task to perform.

If it's conversational, set is_task to false and write a short, friendly
reply yourself -- do not search the web or invent a task to justify using a
tool. If it IS an actionable task, set is_task to true and leave reply empty.
"""


class IntentCheck(BaseModel):
    is_task: bool = Field(
        ..., description="True if the message is an actionable task needing tools; False for chit-chat"
    )
    reply: str = Field(
        default="", description="A short conversational reply, only used when is_task is False"
    )


def check_intent(goal: str, llm: BaseChatModel | None = None) -> IntentCheck:
    """Cheap pre-check: is this an actionable task, or just chat?

    Without this, every input -- including "hi" -- gets forced through
    plan_goal()'s system prompt, which requires at least one tool-using
    step no matter what. In practice that meant the LLM would invent a
    task to justify itself (e.g. treating "hi" as "search the web for
    greeting message examples") rather than just replying. Found live
    testing the UI end to end.

    Falls back to treating the input as a task (is_task=True) on any
    failure — worst case this just degrades to the pre-existing
    always-plan behavior, never a new way to break a request.
    """
    structured_llm = (
        llm.with_structured_output(IntentCheck)
        if llm is not None
        else llm_provider.resilient_structured_llm(IntentCheck)
    )

    try:
        return structured_llm.invoke(
            [SystemMessage(content=_INTENT_SYSTEM_PROMPT), HumanMessage(content=goal)]
        )
    except Exception as exc:  # noqa: BLE001 - any LLM/network failure -> safe fallback, not a crash
        logger.warning("Intent check failed for goal %r, treating as a task: %s", goal, exc)
        return IntentCheck(is_task=True, reply="")


def plan_goal(goal: str, llm: BaseChatModel | None = None) -> list[PlannedStep]:
    """Turn a plain-English goal into an ordered, tool-assigned plan.

    `llm` is injectable so tests can pass a fake that skips the network
    call entirely; production code (the graph node below) leaves it
    unset and gets the real Groq-backed LLM.
    """
    structured_llm = (
        llm.with_structured_output(PlanOutput)
        if llm is not None
        else llm_provider.resilient_structured_llm(PlanOutput)
    )

    try:
        output = structured_llm.invoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=goal)]
        )
    except Exception as exc:  # noqa: BLE001 - any LLM/network failure becomes a clear PlanningError
        raise PlanningError(f"Planning failed for goal {goal!r}: {exc}") from exc

    if not output.steps:
        raise PlanningError(f"Planner produced an empty plan for goal {goal!r}")

    # Don't trust the LLM's own step_number field — renumber 1..n so the
    # executor can safely index into the plan regardless of what came back.
    steps = [
        step.model_copy(update={"step_number": i + 1}) for i, step in enumerate(output.steps)
    ]
    _validate_plan(steps, goal)
    logger.info("Planned %d step(s) for goal %r", len(steps), goal)
    return steps


def _validate_plan(steps: list[PlannedStep], goal: str) -> None:
    """Fail fast, before execution starts, if the plan reads a file
    nothing creates first.

    Found live: the planner sometimes produces a step sequence with a
    read before its matching write ("read conversion.txt" at step 4,
    "save ... to conversion.txt" at step 6 -- plus a duplicate step in
    between). Every individual step was routed correctly (read vs write,
    the right filename) -- the plan's own ordering was just wrong.
    Catching it here means a clear PlanningError at planning time instead
    of discovering it deep into execution, several real steps later.

    Deliberately permissive about *what* counts as "creates it first":
    tracks any filename mentioned in *any* earlier step's description,
    not only file_system write steps specifically -- code_execution can
    write files directly now too (it shares file_system's sandbox), and this
    check has no way to know what a not-yet-generated snippet will
    actually do. Better to miss a real ordering bug occasionally than to
    reject a legitimate plan. Also lets through anything already on disk
    (a file from an earlier run, or one the goal itself references as
    pre-existing) -- not everything a read step names was necessarily
    created by this plan.
    """
    from src.agent.executor import _FILENAME_PATTERN, _extract_filename
    from src.config import get_settings

    sandbox = Path(get_settings().files_sandbox_dir)
    mentioned_files: set[str] = set()

    for step in steps:
        if step.tool == ToolName.FILE_SYSTEM and step.file_action == "read":
            filename = _extract_filename(step.description, goal)
            if filename not in mentioned_files and not (sandbox / filename).exists():
                raise PlanningError(
                    f"Planner produced an out-of-order plan for goal {goal!r}: step "
                    f"{step.step_number} reads {filename!r}, but no earlier step creates it "
                    "and it doesn't already exist."
                )
        for match in _FILENAME_PATTERN.finditer(step.description):
            mentioned_files.add(match.group(1))


def build_planner_graph(llm: BaseChatModel | None = None):
    """LangGraph wrapper around plan_goal: state["goal"] -> state["plan"] (+ bookkeeping)."""

    def plan_node(state: AgentState) -> AgentState:
        plan = plan_goal(state["goal"], llm=llm)
        return {"plan": plan, "current_step": 0, "results": [], "status": TaskStatus.PLANNED}

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.set_entry_point("plan")
    graph.add_edge("plan", END)
    return graph.compile()
