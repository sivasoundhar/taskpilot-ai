"""Shared LLM provider selection: Groq (primary) + local Ollama (fallback).

The project's tech stack locks this pairing in explicitly -- fast, free
Groq inference with a local Ollama model as an offline fallback -- but
for a while nothing in the code actually wired it up: `ollama_base_url`/
`ollama_model` sat in config.py unused, and every LLM call site
(planner.py, executor.py) hardcoded ChatGroq directly with no recovery
path. A real "Connection error" outage was exactly this gap -- Groq was
briefly unreachable and the whole task just failed, with nowhere to fall
back to.

Centralized here, not duplicated per call site, so planner.py and
executor.py get identical fallback behavior from one implementation.
Built on LangChain's own `.with_fallbacks()`: if the primary runnable's
invoke/ainvoke raises *any* exception, the fallback runnable is retried
with the same input automatically, for both sync and async call sites --
no manual try/except needed at each one.
"""
from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable

from src.config import get_settings
from src.runtime_settings import get_runtime_settings


def get_primary_llm() -> BaseChatModel:
    """Built lazily so importing this module never requires GROQ_API_KEY."""
    from langchain_groq import ChatGroq

    settings = get_settings()
    return ChatGroq(model=settings.groq_model, api_key=settings.groq_api_key, temperature=0)


def get_fallback_llm() -> BaseChatModel:
    """Local Ollama -- no API key, works offline. Only needs `ollama serve`
    running with `ollama_model` pulled; if it isn't, the fallback call
    fails too and the original Groq error is what the caller ultimately
    sees (there's genuinely no LLM available at that point)."""
    from langchain_ollama import ChatOllama

    settings = get_settings()
    return ChatOllama(base_url=settings.ollama_base_url, model=settings.ollama_model, temperature=0)


def _ordered(primary: BaseChatModel, fallback: BaseChatModel) -> tuple[BaseChatModel, BaseChatModel]:
    """Applies the Settings page's live provider preference by choosing
    which one is tried *first* -- the other becomes the fallback if the
    first one fails. Defaults to (primary, fallback) as passed in
    ("groq_first", runtime_settings' default) so callers that inject
    explicit fakes for testing are unaffected unless a test deliberately
    flips the preference."""
    if get_runtime_settings().llm_provider_preference == "ollama_first":
        return fallback, primary
    return primary, fallback


def resilient_llm(primary: BaseChatModel | None = None, fallback: BaseChatModel | None = None) -> Runnable:
    """A chat model that transparently retries against the other provider
    if the first one's call raises (auth, rate limit, network/TLS --
    anything). Used by executor.py's plain invoke/ainvoke call sites (code
    + file-content generation). `primary`/`fallback` are injectable so
    tests can pass fakes instead of hitting real Groq/Ollama."""
    primary = primary if primary is not None else get_primary_llm()
    fallback = fallback if fallback is not None else get_fallback_llm()
    first, second = _ordered(primary, fallback)
    return first.with_fallbacks([second])


async def is_ollama_reachable(timeout: float = 1.5) -> bool:
    """Quick liveness check against the local Ollama server -- backs the
    Settings page's fallback status. A plain loopback GET to Ollama's own
    `/api/tags`, not a real generation request, so it's fast either way:
    near-instant when Ollama is running, near-instant "connection refused"
    when it isn't. Deliberately doesn't raise -- any failure just means
    "not reachable right now", which is exactly what the caller wants to
    show, not an error to propagate."""
    import httpx

    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{settings.ollama_base_url}/api/tags")
            return response.status_code == 200
    except Exception:  # noqa: BLE001 - any failure (down, DNS, timeout) just means "not reachable"
        return False


def resilient_structured_llm(
    schema: type, primary: BaseChatModel | None = None, fallback: BaseChatModel | None = None
) -> Runnable:
    """Same fallback behavior, for the `.with_structured_output(schema)`
    call sites (planner.py's plan_goal/check_intent).

    `.with_fallbacks()` has to wrap the *structured* runnable, not the raw
    chat model -- `RunnableWithFallbacks` doesn't itself expose
    `with_structured_output`, so structured output must be applied to each
    of primary/fallback first, then the two combined.
    """
    primary = primary if primary is not None else get_primary_llm()
    fallback = fallback if fallback is not None else get_fallback_llm()
    first, second = _ordered(primary, fallback)
    return first.with_structured_output(schema).with_fallbacks([second.with_structured_output(schema)])
