"""CODE_EXECUTION routing in the executor (src/agent/executor.py).

Covers the step -> generated-code -> executed-code path added for
code_execution. Uses a fake `code_gen_llm` (mirrors planner.py's fake-LLM
tests) so most of this is fast/deterministic; one live test (real Groq
call, needs GROQ_API_KEY) proves the whole path end to end -- skipped
automatically if no key is configured.
"""
from __future__ import annotations

import math
import re

import pytest

from src.agent.executor import _retry_guidance, execute_step
from src.config import get_settings
from src.models import PlannedStep, StepResult, ToolName


class _FakeResponse:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.content = content
        self.response_metadata = {"finish_reason": finish_reason}


class _FakeCodeGenLLM:
    """Stands in for the ChatGroq instance passed as `code_gen_llm`."""

    def __init__(self, code: str, finish_reason: str = "stop") -> None:
        self._code = code
        self._finish_reason = finish_reason

    async def ainvoke(self, _messages):
        return _FakeResponse(self._code, self._finish_reason)


class _BrokenLLM:
    async def ainvoke(self, _messages):
        raise ConnectionError("groq unreachable")


async def test_code_execution_step_generates_and_runs_code():
    step = PlannedStep(step_number=1, tool=ToolName.CODE_EXECUTION, description="add 2 and 2")
    result = await execute_step(step, {"results": []}, code_gen_llm=_FakeCodeGenLLM("print(2 + 2)"))

    assert result.success is True
    assert result.output.strip() == "4"


async def test_code_execution_step_strips_markdown_fences_from_generated_code():
    step = PlannedStep(step_number=1, tool=ToolName.CODE_EXECUTION, description="add 2 and 2")
    fake_llm = _FakeCodeGenLLM("```python\nprint(2 + 2)\n```")
    result = await execute_step(step, {"results": []}, code_gen_llm=fake_llm)

    assert result.success is True
    assert result.output.strip() == "4"


async def test_code_execution_step_failure_is_a_failed_result_not_a_raised_exception():
    step = PlannedStep(step_number=1, tool=ToolName.CODE_EXECUTION, description="divide by zero")
    result = await execute_step(step, {"results": []}, code_gen_llm=_FakeCodeGenLLM("1 / 0"))

    assert result.success is False
    assert "ZeroDivisionError" in result.output


async def test_code_gen_failure_is_also_a_failed_result():
    """If the LLM call itself fails (network, bad key, ...), that's still
    reported as a failed StepResult, not a crash."""
    step = PlannedStep(step_number=1, tool=ToolName.CODE_EXECUTION, description="add 2 and 2")
    result = await execute_step(step, {"results": []}, code_gen_llm=_BrokenLLM())

    assert result.success is False
    assert "Failed to generate code" in result.output


async def test_truncated_code_generation_is_a_clear_failure_not_a_confusing_syntax_error():
    """Regression test: a response cut off by max_tokens used to surface
    as a generic "syntax error" (e.g. "unterminated triple-quoted string")
    with no hint at the real cause. Found live generating a long program."""
    step = PlannedStep(step_number=1, tool=ToolName.CODE_EXECUTION, description="write a long program")
    fake_llm = _FakeCodeGenLLM("print('this got cut off mid", finish_reason="length")

    result = await execute_step(step, {"results": []}, code_gen_llm=fake_llm)

    assert result.success is False
    assert "cut off" in result.output.lower()


# ---------------------------------------------------------------------------
# _retry_guidance(): a real test failure and a blocked-import
# failure need different retry advice, or retries drift toward trying a
# different *technical approach* instead of fixing the *actual bug* the
# first attempt's own output already showed. Found live: after a genuine
# "3 of 5 tests errored" failure, the next 2 retries pivoted to different
# (also-blocked) ways of running the tests instead of debugging the real
# failure.
# ---------------------------------------------------------------------------


def test_retry_guidance_for_a_sandbox_restriction_says_use_a_different_approach():
    guidance = _retry_guidance("Import of 'os' is not allowed")
    assert "sandbox restriction" in guidance.lower()
    assert "different technical approach" in guidance.lower()


def test_retry_guidance_for_a_timeout_says_go_faster():
    guidance = _retry_guidance("Code timed out after 10.0s")
    assert "too long" in guidance.lower()


def test_retry_guidance_for_a_real_test_failure_says_fix_the_bug():
    """The exact case found live: a real traceback/test failure, not a
    denied import -- must NOT get the "try a different approach"
    guidance, or retries drift away from the actual bug."""
    guidance = _retry_guidance(
        "Code exited with status 1: E..EE\nERROR: test_basic_cost\nAssertionError: 24.0 != 25.1"
    )
    assert "real error or test failure" in guidance.lower()
    assert "fix the actual bug" in guidance.lower()
    assert "sandbox restriction" not in guidance.lower()


# ---------------------------------------------------------------------------
# FILE_SYSTEM routing: filename extraction + content generation.
# Regression tests for two related bugs hit in the same session:
# "create snake.py file" saved to a generic "output.txt" (fixed by
# filename extraction), then once that was fixed, the file existed but
# had no real code in it -- just the instruction sentence restated
# (fixed by generating real content when there's no prior step to chain
# from).
# ---------------------------------------------------------------------------


async def _fake_file_system_call_tool(_command, _args, tool_name, tool_args):
    assert tool_name == "write_file"
    return f"Successfully wrote to {tool_args['path']}"


async def test_file_system_step_uses_a_filename_named_in_the_description():
    step = PlannedStep(step_number=1, tool=ToolName.FILE_SYSTEM, description="create snake.py file")
    result = await execute_step(
        step,
        {"results": []},
        file_system_call_tool=_fake_file_system_call_tool,
        code_gen_llm=_FakeCodeGenLLM("import random\nprint('a real snake game')"),
    )

    assert result.success is True
    assert result.output == "Saved to snake.py"


async def test_file_system_step_with_no_prior_step_generates_real_content():
    """The core regression: a single-step "create snake.py" plan used to
    save the literal instruction text into the file instead of real code."""
    step = PlannedStep(step_number=1, tool=ToolName.FILE_SYSTEM, description="create snake.py file")
    generated = "import random\n\ndef main():\n    print('snake game')\n\nmain()\n"
    result = await execute_step(
        step,
        {"results": []},
        file_system_call_tool=_fake_file_system_call_tool,
        code_gen_llm=_FakeCodeGenLLM(generated),
    )

    assert result.success is True
    assert result.content == generated.strip()  # not step.description restated


# ---------------------------------------------------------------------------
# FILE_SYSTEM read routing (file_action="read"). Real regression: this
# branch didn't exist before -- every file_system step, "read" or not, fell
# through to the write path above, generating NEW content and overwriting
# the target. A real "read snake.py and explain it" request silently
# destroyed snake.py's actual content; an intentional "read a nonexistent
# file" error-handling test silently "succeeded" by fabricating and saving
# fake content instead of failing. Found live during multi-task
# validation.
# ---------------------------------------------------------------------------


async def _fake_file_read_call_tool(_command, _args, tool_name, tool_args):
    assert tool_name == "read_text_file"
    if tool_args["path"] == "missing.txt":
        from src.tools.mcp_client import McpToolError

        raise McpToolError("ENOENT: no such file or directory")
    return f"contents of {tool_args['path']}"


async def test_file_system_read_step_returns_the_actual_file_content():
    step = PlannedStep(
        step_number=1, tool=ToolName.FILE_SYSTEM, file_action="read", description="read snake.py and explain it"
    )
    result = await execute_step(step, {"results": []}, file_system_call_tool=_fake_file_read_call_tool)

    assert result.success is True
    assert result.output == "contents of snake.py"
    assert result.content == "contents of snake.py"


async def test_file_system_read_step_never_calls_the_code_gen_llm():
    """The exact regression: reading must never generate/overwrite content.
    A code_gen_llm that raises if invoked proves generation is never even
    attempted on the read path."""
    step = PlannedStep(step_number=1, tool=ToolName.FILE_SYSTEM, file_action="read", description="read snake.py")
    result = await execute_step(
        step,
        {"results": []},
        file_system_call_tool=_fake_file_read_call_tool,
        code_gen_llm=_BrokenLLM(),
    )

    assert result.success is True  # would have failed if it had tried to generate via _BrokenLLM


async def test_file_system_read_step_missing_file_is_a_failed_result():
    step = PlannedStep(
        step_number=1, tool=ToolName.FILE_SYSTEM, file_action="read", description="read missing.txt"
    )
    result = await execute_step(step, {"results": []}, file_system_call_tool=_fake_file_read_call_tool)

    assert result.success is False
    assert "missing.txt" in result.output


async def test_file_system_step_without_file_action_still_defaults_to_write():
    """Backward compatibility: file_action is optional (an older/fake plan
    that never set it) -- must still write, the pre-existing behavior."""
    step = PlannedStep(step_number=1, tool=ToolName.FILE_SYSTEM, description="create snake.py file")
    assert step.file_action is None
    result = await execute_step(
        step,
        {"results": []},
        file_system_call_tool=_fake_file_system_call_tool,
        code_gen_llm=_FakeCodeGenLLM("print('ok')"),
    )

    assert result.success is True
    assert result.output == "Saved to snake.py"


async def test_file_system_step_generates_content_using_prior_result_as_context():
    """Superseded test, kept as a regression marker: chaining used to skip
    LLM generation entirely and save the prior step's output verbatim --
    fine for "save the findings" but wrong for "save AS cities.csv with
    columns X,Y" (a real formatting request). Found live: a verbatim-copy
    chain saved the *entire raw web_search dump* as "cities.csv" -- not a
    CSV at all. Chaining now always goes through
    _generate_file_content, using the prior result as context (the LLM
    decides what to actually write, per the step's own description)."""
    prior_result = StepResult(step_number=1, tool=ToolName.CODE_EXECUTION, success=True, output="42")
    step = PlannedStep(step_number=2, tool=ToolName.FILE_SYSTEM, description="save the result")
    fake_llm = _RecordingCodeGenLLM("42")

    result = await execute_step(
        step,
        {"results": [prior_result]},
        file_system_call_tool=_fake_file_system_call_tool,
        code_gen_llm=fake_llm,
    )

    assert result.success is True
    assert result.content == "42"
    assert "42" in str(fake_llm.last_messages)  # the prior result reached the prompt as context


class _RecordingCodeGenLLM:
    """Like _FakeCodeGenLLM, but remembers the messages it was called
    with, so a test can inspect what context actually reached the prompt."""

    def __init__(self, code: str) -> None:
        self._code = code
        self.last_messages: list | None = None

    async def ainvoke(self, messages):
        self.last_messages = messages
        return _FakeResponse(self._code)


async def test_file_system_chains_the_real_content_of_a_prior_write_not_its_confirmation():
    """Regression: chaining after a file_system *write* step used to pass
    its short "Saved to X" confirmation (StepResult.output) instead of the
    actual saved content (StepResult.content)."""
    prior_write = StepResult(
        step_number=1,
        tool=ToolName.FILE_SYSTEM,
        success=True,
        output="Saved to draft.txt",
        content="the real draft content",
    )
    step = PlannedStep(step_number=2, tool=ToolName.FILE_SYSTEM, description="save a copy as final.txt")

    result = await execute_step(
        step,
        {"results": [prior_write]},
        file_system_call_tool=_fake_file_system_call_tool,
        # Deterministic, matching every other test in this file -- this
        # one was accidentally hitting the real default LLM (no fake
        # given), which happened to relay content verbatim until Groq got
        # rate-limited from today's heavy live testing and fell back to
        # Ollama, which paraphrases instead. Found live, not by design.
        code_gen_llm=_FakeCodeGenLLM("the real draft content"),
    )

    assert result.content == "the real draft content"


async def test_code_execution_chains_the_real_content_of_a_prior_write_not_its_confirmation():
    """The exact bug found live: "save cities.csv, then read and process
    it" -- code_execution's prompt context was the confirmation string
    "Saved to cities.csv", not the real CSV data, so the LLM (correctly,
    per its own "don't invent data" instruction) tried to open() the file
    itself instead -- which the sandbox blocks."""
    prior_write = StepResult(
        step_number=1,
        tool=ToolName.FILE_SYSTEM,
        success=True,
        output="Saved to cities.csv",
        content="city,population\nDelhi,23390383\nMumbai,20961472\n",
    )
    step = PlannedStep(
        step_number=2, tool=ToolName.CODE_EXECUTION, description="read cities.csv and sum the population"
    )
    fake_llm = _RecordingCodeGenLLM("print('ok')")

    await execute_step(step, {"results": [prior_write]}, code_gen_llm=fake_llm)

    prompt_text = str(fake_llm.last_messages)
    assert "23390383" in prompt_text  # the real data reached the prompt
    assert "Saved to cities.csv" not in prompt_text  # not just the confirmation


async def test_file_system_step_falls_back_to_default_when_no_filename_named():
    step = PlannedStep(step_number=1, tool=ToolName.FILE_SYSTEM, description="save the findings")
    result = await execute_step(
        step,
        {"results": []},
        file_system_call_tool=_fake_file_system_call_tool,
        code_gen_llm=_FakeCodeGenLLM("the findings, written up"),
    )

    assert result.success is True
    assert result.output == "Saved to output.txt"


async def test_file_system_step_falls_back_to_the_goal_when_its_own_description_names_no_file():
    """Regression, found live and reproducible 4/4 times: the planner's
    own step description sometimes paraphrases away a filename the user
    explicitly named in the goal (e.g. "Create breakout.py: ..." becomes
    the step description "Create a new file for the game") -- even after
    strengthening the planner's prompt not to do that. This is the
    deterministic backstop: the *goal* text still has the real filename,
    so a step whose own description names none falls back to searching
    the goal before giving up to the generic placeholder."""
    step = PlannedStep(step_number=1, tool=ToolName.FILE_SYSTEM, description="create a new file for the game")
    result = await execute_step(
        step,
        {"results": [], "goal": "Create breakout.py: Brick Breaker with pygame."},
        file_system_call_tool=_fake_file_system_call_tool,
        code_gen_llm=_FakeCodeGenLLM("game skeleton code"),
    )

    assert result.success is True
    assert result.output == "Saved to breakout.py"


async def test_file_system_step_does_not_mistake_a_decimal_number_for_a_filename():
    """A description mentioning a plain number like "10.5" must not be
    misread as a filename ("5" isn't a real extension)."""
    step = PlannedStep(
        step_number=1, tool=ToolName.FILE_SYSTEM, description="save the result, which is 10.5, to a file"
    )
    result = await execute_step(
        step,
        {"results": []},
        file_system_call_tool=_fake_file_system_call_tool,
        code_gen_llm=_FakeCodeGenLLM("10.5"),
    )

    assert result.output == "Saved to output.txt"


async def test_file_system_content_generation_failure_is_a_failed_result():
    step = PlannedStep(step_number=1, tool=ToolName.FILE_SYSTEM, description="create notes.txt")
    result = await execute_step(step, {"results": []}, code_gen_llm=_BrokenLLM())

    assert result.success is False
    assert "Failed to generate file content" in result.output


@pytest.mark.skipif(not get_settings().groq_api_key, reason="requires GROQ_API_KEY")
async def test_code_execution_step_live_end_to_end():
    """Real Groq call generates the code, then it actually runs. Proves
    the "agent runs Python code and returns correct output" requirement
    end to end, not just against fakes."""
    step = PlannedStep(
        step_number=1,
        tool=ToolName.CODE_EXECUTION,
        description=(
            "Calculate compound interest on a principal of 10000 at an annual "
            "rate of 5% over 10 years, and print only the final amount rounded "
            "to 2 decimal places"
        ),
    )

    result = await execute_step(step, {"results": []})

    assert result.success is True, result.output
    numbers = re.findall(r"[\d,]+\.\d+", result.output)
    assert numbers, f"No number found in output: {result.output!r}"
    value = float(numbers[0].replace(",", ""))
    assert math.isclose(value, 16288.95, rel_tol=1e-3)
