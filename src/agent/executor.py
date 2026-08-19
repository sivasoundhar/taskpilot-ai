"""Executor: runs each planned step by routing to the matching tool.

execute_step() runs one step (routing + result); run_plan() drives the
whole plan — chaining results between steps, updating AgentState after
each one, and yielding a StepUpdate per step so a caller can stream
progress live (the seam the SSE endpoint consumes). src/agent/graph.py
wires plan_goal() + run_plan() into one LangGraph loop.
"""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.agent import llm_provider
from src.agent.state import AgentState
from src.models import PlannedStep, StepResult, StepStatus, StepUpdate, TaskStatus, ToolName
from src.runtime_settings import get_runtime_settings
from src.tools import code_execution, file_system, web_search

logger = logging.getLogger("taskpilot.agent.executor")

# Matches src/tools/{web_search,file_system}.py's CallTool type. Threaded
# through execute_step/run_plan so tests can fake the MCP layer for
# web_search/file_system the same way code_gen_llm fakes the LLM layer for
# code_execution — the whole routing/chaining loop becomes testable
# without spawning a single real subprocess.
CallTool = Callable[[str, Sequence[str], str, dict], Awaitable[str]]

# A PlannedStep is just a free-text `description`, with no separate
# path/content fields, so a file_system step can't yet know *which* file
# to write. Default filename until the planner's structured output (or an
# LLM call at execution time) can decide one.
_DEFAULT_WRITE_PATH = "output.txt"

# Matches a filename the goal actually named -- "snake.py", "report.txt",
# "notes.md" -- requiring the extension to start with a letter so plain
# decimals ("10.5") in a description never get misread as a filename.
_FILENAME_PATTERN = re.compile(r"\b([\w][\w-]*\.[a-zA-Z][a-zA-Z0-9]{0,9})\b")


def _extract_filename(description: str, goal: str | None = None) -> str:
    """Best-effort: use a filename the step description actually named.

    Falls back to searching the *original goal* text if the step's own
    description doesn't have one -- the planner's own step descriptions
    are a paraphrase, and paraphrasing sometimes drops a filename the
    user explicitly named (e.g. "Create breakout.py: Brick Breaker..."
    becomes the step description "Create a new file for the game",
    which the SYSTEM_PROMPT explicitly asks the planner not to do, but
    doesn't reliably prevent -- confirmed live, reproducible 4/4 times
    even after strengthening that instruction). The prompt fix stays
    (it's still correct guidance), but this is the deterministic
    backstop that doesn't depend on the LLM reliably following it. Only
    the generic placeholder remains if *neither* text names a file.
    """
    match = _FILENAME_PATTERN.search(description)
    if match:
        return match.group(1)
    if goal:
        match = _FILENAME_PATTERN.search(goal)
        if match:
            return match.group(1)
    return _DEFAULT_WRITE_PATH


def _chain_context(prior_results: list[StepResult]) -> str | None:
    """The real data from the most recent step, for chaining into the next
    one. Prefers `content` over `output` when both exist -- for a
    file_system step, `output` is just a short "Saved to X" confirmation
    (`content` is always the actual file content), while for web_search /
    code_execution / a file_system read, `output` already *is* the real
    data and `content` is None, so this falls through to `output` there.

    Chaining on `output` alone used to silently pass the confirmation
    string instead of real data whenever the prior step was a file_system
    write. Found live: "save cities.csv, then read and process it" --
    code_execution had no real data in its prompt, and (correctly, per its
    own system prompt's "don't invent data" instruction) tried to open()
    the file itself instead of hallucinating numbers -- which the sandbox
    correctly blocks.
    """
    if not prior_results:
        return None
    last = prior_results[-1]
    return last.content if last.content is not None else last.output


class CodeGenerationTruncatedError(RuntimeError):
    """Raised when the code-gen LLM's response was cut off by max_tokens
    before it finished -- caught and reported as `execute_step`'s own
    failed StepResult, same as any other code-gen failure."""


_CODE_GEN_SYSTEM_PROMPT = """You write a single, self-contained Python script
that accomplishes exactly one step of a plan. If context from a previous
step is given, treat it as the real data to work with -- embed it in the
script as needed and use it as-is. Never invent or guess a fact (a
version number, a statistic, a name) that the context should supply; if
the context doesn't contain what's needed, say so in the printed output
instead of making something up. print() whatever the result is -- stdout
is the only channel the caller reads back. Output ONLY the Python code:
no markdown code fences, no explanation, no comments about what you're
doing.

Your code runs with the current directory set to the same sandbox
directory earlier file_system steps saved files into -- open('data.json')
or `import helper_module` for a .py file an earlier step wrote both work
for real, reading the actual file. Only Python's standard library is
installed (no pygame, numpy, requests, etc.) -- don't import anything
else.

If the step asks you to "run" a script an earlier step wrote (e.g.
converter.py), and that script defines its logic inside a
`def main():` guarded by `if __name__ == "__main__":` (a common,
idiomatic pattern), a bare `import that_script` does NOT execute
main() -- importing only runs top-level code. You must call the
function explicitly, e.g. `import converter; converter.main()`, or
the step will silently do nothing and produce no output."""

# Prior-step output folded into the code-gen prompt for chaining (e.g.
# code_execution analyzing what web_search actually found). Capped so one
# huge search result doesn't blow out the prompt.
_MAX_CONTEXT_CHARS = 4_000


def _default_code_gen_llm() -> BaseChatModel:
    """Built lazily so importing this module never requires GROQ_API_KEY.

    max_tokens is explicit: with no override, a long enough generated
    program (found live with a full game implementation) can get cut off
    mid-statement, producing code that fails to parse with a confusing
    "syntax error" instead of the real cause (truncation). First attempt
    used 8192 -- too generous, Groq rejected the whole request with a
    413 "Request too large" for this model rather than just truncating
    the response. 4096 is the safer middle ground: well past a single
    math/data/logic script's real needs, without tripping the same limit.

    Wrapped with the local Ollama fallback (llm_provider.resilient_llm) so
    a Groq outage degrades to a slower local model instead of failing the
    step outright -- same reasoning as planner.py's structured-output call
    sites. Only the Groq side needs the explicit max_tokens; the fallback
    gets llm_provider's plain default.
    """
    from langchain_groq import ChatGroq

    from src.config import get_settings

    settings = get_settings()
    primary = ChatGroq(
        model=settings.groq_model, api_key=settings.groq_api_key, temperature=0, max_tokens=4096
    )
    return llm_provider.resilient_llm(primary=primary)


async def _invoke_and_extract(system_prompt: str, user_content: str, llm: BaseChatModel | None) -> str:
    """Shared by _generate_code and _generate_file_content: call the LLM,
    catch a truncated response before it becomes a confusing downstream
    failure, strip markdown fences if the model added them anyway."""
    llm = llm if llm is not None else _default_code_gen_llm()
    response = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_content)])

    # A truncated response (hit max_tokens mid-statement) produces code
    # that fails to parse -- but with a confusing "syntax error" instead
    # of naming the actual cause. Catch it here instead, while we still
    # have finish_reason on hand. response_metadata is populated by real
    # ChatGroq responses; fakes in tests may not set it, hence .get(...).
    if getattr(response, "response_metadata", {}).get("finish_reason") == "length":
        raise CodeGenerationTruncatedError(
            "The generated content was cut off before it finished (hit the model's output limit)."
        )

    text = str(response.content).strip()

    # Strip markdown fences if the model added them despite instructions --
    # LLMs do this often enough that defending against it is cheaper than
    # re-prompting.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _retry_guidance(previous_error: str) -> str:
    """Different failures need different retry guidance -- a sandbox
    restriction needs a different *approach*; a real test/logic failure
    needs the actual bug fixed, not another way to run the same broken
    code. Found live: after a genuine test failure (3 of 5 tests
    actually ran and errored), the next 2 retries pivoted to different
    ways of *running* the tests (both also blocked) instead of fixing
    what the first attempt's own output already showed was wrong."""
    if "is not allowed" in previous_error:
        return (
            "This failed because of a SANDBOX RESTRICTION (a denied import/call/path), not a bug in "
            "your logic. Don't try a different technical approach to work around it -- use only what's "
            "already described as available above (standard library, open()/import for files already "
            "in the working directory)."
        )
    if "timed out" in previous_error:
        return "This failed because it took too long. Write a simpler, faster approach."
    return (
        "This failed with a REAL error or test failure -- read it carefully and fix the actual bug in "
        "your logic (or the test's own expectations, if the test itself was wrong). Don't just try a "
        "different technical approach to running the same broken code."
    )


async def _generate_code(
    description: str,
    context: str | None = None,
    previous_error: str | None = None,
    llm: BaseChatModel | None = None,
) -> str:
    """Turn a step's natural-language description into runnable Python.

    A PlannedStep only carries free text -- code_execution needs an
    actual script, so this translation happens first. `llm` is
    injectable so tests can pass a fake, mirroring planner.py's pattern.

    `context` (the prior step's output, when chained) matters more than it
    looks: without it, "analyze what was just found" has no real data to
    analyze, and the LLM will confidently invent a plausible-sounding but
    wrong answer instead. Found live testing chained goals end to end.

    `previous_error`: set by run_plan()'s retry loop when this is
    a re-attempt after a failed run -- folds the real error back into the
    prompt so the regenerated code has an actual reason to be different,
    not just another guess: if the script fails, fix it and rerun until
    it succeeds.
    """
    user_content = description
    if context:
        user_content = f"Context from the previous step:\n{context[:_MAX_CONTEXT_CHARS]}\n\nTask: {description}"
    if previous_error:
        user_content += (
            f"\n\nYour previous attempt at this failed with this error:\n"
            f"{previous_error[:_MAX_CONTEXT_CHARS]}\n\n{_retry_guidance(previous_error)}"
        )
    return await _invoke_and_extract(_CODE_GEN_SYSTEM_PROMPT, user_content, llm)


_FILE_CONTENT_GEN_SYSTEM_PROMPT = """You write the actual content that should
be saved to a file, based on a task description and the target filename. If
the filename's extension implies code (.py, .js, .html, ...), write real,
complete, working code for what's being asked -- not a description of what
the code should do. If context from a previous step is given, use it as the
real data / basis for what you write; otherwise write the content yourself
from the task description. Output ONLY the file content: no markdown code
fences, no explanation, no meta-commentary about what you're doing."""


async def _generate_file_content(
    description: str, filename: str, context: str | None = None, llm: BaseChatModel | None = None
) -> str:
    """Generate what should actually be saved to `filename`.

    Without this, a file_system step with nothing to chain from (a
    single-step "create snake.py" plan, say) fell back to saving the
    step's own instruction sentence as the file's content -- the file
    got created, but with no actual code/content in it. Found live.
    """
    user_content = f"Filename: {filename}\nTask: {description}"
    if context:
        user_content = f"Context from a previous step:\n{context[:_MAX_CONTEXT_CHARS]}\n\n{user_content}"
    return await _invoke_and_extract(_FILE_CONTENT_GEN_SYSTEM_PROMPT, user_content, llm)


async def execute_step(
    step: PlannedStep,
    state: AgentState,
    *,
    code_gen_llm: BaseChatModel | None = None,
    web_search_call_tool: CallTool | None = None,
    file_system_call_tool: CallTool | None = None,
    previous_error: str | None = None,
) -> StepResult:
    """Run the tool for one step, routing on `step.tool`.

    Never raises for a tool-level failure — a failed StepResult is
    returned instead, so run_plan() can decide whether to continue or
    stop: a tool failing should mean the agent recovers or reports, not
    crashes.

    `previous_error`: only meaningful for CODE_EXECUTION -- set by
    run_plan()'s retry loop on a re-attempt, so the regenerated code gets
    the real failure reason, not just another blind guess.
    """
    if step.tool == ToolName.WEB_SEARCH:
        try:
            kwargs = {"_call_tool": web_search_call_tool} if web_search_call_tool else {}
            # Live-tunable from the Settings page (default 5) -- not a
            # hardcoded constant, so "search results per query" there is a
            # real control, not a label.
            max_results = get_runtime_settings().web_search_max_results
            output = await web_search.run(step.description, max_results=max_results, **kwargs)
            return StepResult(step_number=step.step_number, tool=step.tool, success=True, output=output)
        except web_search.WebSearchError as exc:
            return StepResult(
                step_number=step.step_number, tool=step.tool, success=False, output=str(exc)
            )

    if step.tool == ToolName.FILE_SYSTEM and step.file_action == "read":
        # Found live during multi-task validation: this branch never
        # existed before -- every file_system step, "read" or not, fell
        # through to the write path below, which generates NEW content and
        # overwrites the target. A real "read snake.py and explain it"
        # request silently destroyed snake.py's actual content; an
        # intentional "read a file that doesn't exist" error-handling test
        # silently "succeeded" by fabricating and saving fake content
        # instead of failing. file_action is planner.py's
        # explicit read/write decision (SYSTEM_PROMPT) -- not a guess from
        # keywords in the free-text description, which would be one typo
        # away from the exact bug this fixes.
        read_path = _extract_filename(step.description, state.get("goal"))
        try:
            kwargs = {"_call_tool": file_system_call_tool} if file_system_call_tool else {}
            content = await file_system.read(read_path, **kwargs)
            return StepResult(
                step_number=step.step_number,
                tool=step.tool,
                success=True,
                output=content,
                content=content,
            )
        except file_system.FileSystemError as exc:
            return StepResult(
                step_number=step.step_number, tool=step.tool, success=False, output=str(exc)
            )

    if step.tool == ToolName.FILE_SYSTEM:
        # Filename: use whatever name the goal actually specified (e.g.
        # "create snake.py") when there is one, falling back to a generic
        # placeholder otherwise -- found necessary live: a goal naming
        # snake.py explicitly was still saved as output.txt.
        #
        # file_action is "write" or None here (None: an older/fake plan
        # that never set it -- defaults to write, the pre-existing
        # behavior, so nothing already relying on it breaks).
        prior_results = state.get("results") or []
        write_path = _extract_filename(step.description, state.get("goal"))

        # Always generate through the LLM -- even when chaining from a
        # prior step -- rather than copying the prior step's content
        # verbatim. Chaining used to take that shortcut (skip generation
        # entirely, just save prior_results[-1]'s data as-is): fine for
        # "save the findings" where the raw findings ARE the report, but
        # wrong for "save AS cities.csv with columns city,population" --
        # a real request to *transform* the data, not relay it untouched.
        # Found live: a "save as cities.csv" step saved the entire raw
        # web_search dump (5 sources, markdown tables, unrelated page
        # content) verbatim -- not CSV at all, which then broke the next
        # code_execution step trying to actually read it as one.
        # _generate_file_content already treats context as
        # "the basis for what you write," not something to copy blindly,
        # so this also improves plain "save a report" quality generally --
        # it can be a real report the LLM wrote, not a raw scrape dump.
        chained_context = _chain_context(prior_results) if prior_results else None
        try:
            content = await _generate_file_content(
                step.description, write_path, context=chained_context, llm=code_gen_llm
            )
        except Exception as exc:  # noqa: BLE001 - content-gen (LLM call) failures, not just write ones
            logger.exception(
                "File content generation failed for step %d (%r)", step.step_number, step.description
            )
            return StepResult(
                step_number=step.step_number,
                tool=step.tool,
                success=False,
                output=f"Failed to generate file content: {exc}",
            )

        try:
            kwargs = {"_call_tool": file_system_call_tool} if file_system_call_tool else {}
            await file_system.write(write_path, content, **kwargs)
            return StepResult(
                step_number=step.step_number,
                tool=step.tool,
                success=True,
                output=f"Saved to {write_path}",
                content=content,
            )
        except file_system.FileSystemError as exc:
            return StepResult(
                step_number=step.step_number, tool=step.tool, success=False, output=str(exc)
            )

    if step.tool == ToolName.CODE_EXECUTION:
        # Same "most recent prior step" chaining as file_system above --
        # without this, "analyze the findings" has no findings to analyze
        # and the LLM invents a plausible-but-wrong answer instead.
        prior_results = state.get("results") or []
        context = _chain_context(prior_results)
        try:
            code = await _generate_code(
                step.description, context=context, previous_error=previous_error, llm=code_gen_llm
            )
            output = await code_execution.run(code)
            return StepResult(step_number=step.step_number, tool=step.tool, success=True, output=output)
        except code_execution.CodeExecutionError as exc:
            return StepResult(
                step_number=step.step_number, tool=step.tool, success=False, output=str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - code-gen (LLM call) failures, not just execution ones
            logger.exception("Code generation failed for step %d (%r)", step.step_number, step.description)
            return StepResult(
                step_number=step.step_number,
                tool=step.tool,
                success=False,
                output=f"Failed to generate code: {exc}",
            )

    raise AssertionError(f"Unhandled tool: {step.tool!r}")  # ToolName only has 3 members; unreachable


# Only CODE_EXECUTION steps retry (if the script fails, fix it and rerun
# until it succeeds). Scoped deliberately
# narrow, not applied to web_search/file_system too: a failed search or
# save is usually an infra/config problem (bad key, sandbox rejection),
# not something regenerating the *code* around it can fix -- retrying
# those would just repeat the same failure 3x instead of surfacing it
# clearly. 3 = 1 initial attempt + 2 retries; bounded so a genuinely
# broken request still ends in a clear failure, not a silent infinite loop.
_MAX_CODE_EXECUTION_ATTEMPTS = 3


async def run_plan(
    state: AgentState,
    *,
    code_gen_llm: BaseChatModel | None = None,
    web_search_call_tool: CallTool | None = None,
    file_system_call_tool: CallTool | None = None,
) -> AsyncIterator[StepUpdate]:
    """Run every step in state['plan'] in order, chaining results.

    An async generator, not a plain function: it yields a StepUpdate for
    each step's start (RUNNING) and end (DONE/FAILED) as they happen —
    the seam the SSE endpoint streams from directly. `state` is
    mutated in place (results/current_step/status) so a caller that
    doesn't care about the live events can just drain the generator and
    read `state` afterward (this is what graph.py's execute_node does).

    Error-handling decision: STOP on the first step that's still failed
    after retries, rather than continue -- a tool failing should mean the
    agent recovers or reports, not silently corrupts later steps. Later
    steps are usually chained off earlier ones (e.g. file_system saving
    web_search's findings) — continuing past a failure would mean saving
    garbage or computing on missing data, which is worse than stopping
    and reporting clearly. Revisit only if a real task needs partial
    credit for independent (non-chained) steps.

    CODE_EXECUTION steps get up to _MAX_CODE_EXECUTION_ATTEMPTS tries:
    a failed attempt's error is fed back into the next attempt's
    code-gen prompt (see _generate_code's previous_error), and each
    attempt streams its own RUNNING/FAILED StepUpdate -- the retry is
    visible live, not hidden, matching the live-UI design (you watch
    the agent actually recover, not just eventually succeed silently).
    """
    plan = state.get("plan") or []
    state["results"] = state.get("results") or []
    state["status"] = TaskStatus.EXECUTING

    for step in plan:
        state["current_step"] = step.step_number - 1
        max_attempts = _MAX_CODE_EXECUTION_ATTEMPTS if step.tool == ToolName.CODE_EXECUTION else 1
        previous_error: str | None = None
        result: StepResult | None = None

        for attempt in range(1, max_attempts + 1):
            retry_note = f" (attempt {attempt}/{max_attempts})" if max_attempts > 1 else ""
            yield StepUpdate(
                step_number=step.step_number,
                tool=step.tool,
                status=StepStatus.RUNNING,
                message=f"Running {step.tool.value}{retry_note}: {step.description}",
            )

            try:
                result = await execute_step(
                    step,
                    state,
                    code_gen_llm=code_gen_llm,
                    web_search_call_tool=web_search_call_tool,
                    file_system_call_tool=file_system_call_tool,
                    previous_error=previous_error,
                )
            except Exception as exc:  # noqa: BLE001 - execute_step's 3 branches already catch their own
                # tool errors into a failed StepResult; this is a last-resort net
                # for anything unexpected (a 4th tool added without matching
                # error handling, a bug) so run_plan itself never crashes.
                result = StepResult(
                    step_number=step.step_number, tool=step.tool, success=False, output=f"Unexpected error: {exc}"
                )

            if result.success:
                break

            previous_error = result.output
            if attempt < max_attempts:
                yield StepUpdate(
                    step_number=step.step_number,
                    tool=step.tool,
                    status=StepStatus.FAILED,
                    message=f"Attempt {attempt} failed: {result.output[:200]} — retrying...",
                    result=result.output,
                )

        state["results"].append(result)

        if result.success:
            yield StepUpdate(
                step_number=step.step_number,
                tool=step.tool,
                status=StepStatus.DONE,
                message=result.output[:200],
                result=result.output,
                content=result.content,
            )
        else:
            yield StepUpdate(
                step_number=step.step_number,
                tool=step.tool,
                status=StepStatus.FAILED,
                message=result.output[:200],
                result=result.output,
            )
            state["status"] = TaskStatus.FAILED
            return

    state["status"] = TaskStatus.DONE
