"""Pydantic schemas shared across the API, agent, and streaming layers.

Kept in one place so the request/response shape the frontend depends on
has a single source of truth.
"""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ToolName(str, Enum):
    """The 3 tools — fixed, do not add more."""

    WEB_SEARCH = "web_search"
    FILE_SYSTEM = "file_system"
    CODE_EXECUTION = "code_execution"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class TaskStatus(str, Enum):
    """Overall AgentState.status — coarser than per-step StepStatus."""

    PLANNING = "planning"
    PLANNED = "planned"
    EXECUTING = "executing"
    DONE = "done"
    FAILED = "failed"


class TaskRequest(BaseModel):
    """What the frontend sends to POST /run."""

    goal: str = Field(..., min_length=1, description="The user's task in plain English")


class PlannedStep(BaseModel):
    """One step of the agent's plan, before it has been executed."""

    step_number: int
    tool: ToolName
    description: str = Field(..., description="What this step will do")
    file_action: Literal["read", "write"] | None = Field(
        default=None,
        description=(
            "Only meaningful when tool is file_system: 'read' (inspect an existing file) or "
            "'write' (create/save/overwrite one). None for the other 2 tools."
        ),
    )


class StepUpdate(BaseModel):
    """A single streamed event describing a step's progress.

    One or more of these is emitted per step (e.g. running, then done)
    so the UI can render live step cards as the agent works.
    """

    step_number: int
    tool: ToolName
    status: StepStatus
    message: str = Field(..., description="Human-readable status, e.g. 'Found 8 articles'")
    result: str | None = Field(default=None, description="Tool output, once done")
    content: str | None = Field(
        default=None,
        description="Full content behind a file_system save (generated or chained), for the UI's preview",
    )


class StepResult(BaseModel):
    """Output of one executed step, stored in AgentState.results.

    This is what lets step N+1 use step N's output (e.g. code_execution
    reading the text web_search found) — the executor (Days 3-6) chains
    steps by reading prior entries in this list.
    """

    step_number: int
    tool: ToolName
    success: bool
    output: str
    content: str | None = Field(
        default=None,
        description=(
            "For a successful file_system step: the actual content that was saved (generated or "
            "chained from a prior step), distinct from `output`'s short 'Saved to X' confirmation — "
            "lets the UI preview what's actually in the file without a separate read."
        ),
    )


class TaskResult(BaseModel):
    """The final event, once all steps complete (or the agent gives up)."""

    goal: str
    success: bool
    summary: str
    steps_completed: int
    steps_total: int


class ChatMessage(BaseModel):
    """A plain conversational reply — sent instead of a plan when the
    input isn't an actionable task (a greeting, small talk, etc.). See
    src/agent/planner.py's check_intent()."""

    message: str


class RuntimeSettingsUpdate(BaseModel):
    """What the Settings page's PATCH /settings sends — the few knobs
    live-editable without a restart (src/runtime_settings.py). Every
    field optional: the frontend sends only what actually changed."""

    llm_provider_preference: str | None = Field(
        default=None, description='"groq_first" or "ollama_first"'
    )
    log_level: str | None = Field(default=None, description="DEBUG/INFO/WARNING/ERROR")
    web_search_max_results: int | None = Field(default=None, ge=1, le=20)
