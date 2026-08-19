"""Agent state schema.

This is the object LangGraph threads through the planner -> executor
graph. The planner node (src/agent/planner.py) populates
goal -> plan/current_step/status; the executor appends to `results`
as each step completes.
"""
from typing import TypedDict

from src.models import PlannedStep, StepResult, TaskStatus


class AgentState(TypedDict, total=False):
    """LangGraph state dict.

    total=False because fields are populated incrementally as the
    graph progresses (goal -> plan -> execute -> ...), not all at once.
    """

    goal: str
    plan: list[PlannedStep]
    current_step: int
    results: list[StepResult]
    status: TaskStatus
