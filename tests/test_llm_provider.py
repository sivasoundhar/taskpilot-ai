"""Groq -> Ollama fallback (the project's locked tech stack,
implemented after a real "Connection error" outage).

Fakes duck-type just enough of BaseChatModel/Runnable (.invoke/.ainvoke,
.with_structured_output, .with_fallbacks is LangChain's own, not faked)
to prove the fallback actually fires on a primary failure and is skipped
when the primary succeeds -- without any real Groq/Ollama call.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from src.agent import llm_provider


class _FakeChatModel:
    """Minimal Runnable: raises on invoke/ainvoke if given an Exception,
    otherwise returns a fixed message-like object."""

    def __init__(self, result):
        self._result = result

    def invoke(self, _messages, **_kwargs):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    async def ainvoke(self, _messages, **_kwargs):
        return self.invoke(_messages)

    def with_fallbacks(self, fallbacks):
        # Mirrors LangChain's real with_fallbacks semantics closely enough
        # for these tests: try self, then each fallback in order.
        primary = self

        class _WithFallbacks:
            def invoke(self, messages, **kwargs):
                for runnable in [primary, *fallbacks]:
                    try:
                        return runnable.invoke(messages, **kwargs)
                    except Exception:
                        continue
                raise RuntimeError("all runnables failed")

            async def ainvoke(self, messages, **kwargs):
                return self.invoke(messages, **kwargs)

        return _WithFallbacks()

    def with_structured_output(self, _schema):
        return self


class _Schema(BaseModel):
    value: str


def test_resilient_llm_uses_primary_when_it_succeeds():
    primary = _FakeChatModel("primary result")
    fallback = _FakeChatModel("fallback result")
    llm = llm_provider.resilient_llm(primary=primary, fallback=fallback)
    assert llm.invoke([]) == "primary result"


def test_resilient_llm_falls_back_to_ollama_when_groq_fails():
    """The exact scenario this feature exists for: Groq's call raises
    (connection error, rate limit, auth) -- the same request is retried
    against the local Ollama fallback instead of failing outright."""
    primary = _FakeChatModel(ConnectionError("groq unreachable"))
    fallback = _FakeChatModel("fallback result")
    llm = llm_provider.resilient_llm(primary=primary, fallback=fallback)
    assert llm.invoke([]) == "fallback result"


def test_resilient_llm_respects_the_ollama_first_setting_page_preference():
    """The Settings page's provider-preference toggle has to actually
    reorder which one is tried first, not just be a label -- both fakes
    here would succeed, so which result comes back proves which one was
    tried first, not just "a fallback happened"."""
    from src.runtime_settings import update_runtime_settings

    update_runtime_settings(llm_provider_preference="ollama_first")
    primary = _FakeChatModel("groq result")
    fallback = _FakeChatModel("ollama result")
    llm = llm_provider.resilient_llm(primary=primary, fallback=fallback)
    assert llm.invoke([]) == "ollama result"


@pytest.mark.asyncio
async def test_resilient_llm_falls_back_on_async_call_too():
    """executor.py's code/file-content generation calls .ainvoke(), not
    .invoke() -- fallback has to work on both paths."""
    primary = _FakeChatModel(ConnectionError("groq unreachable"))
    fallback = _FakeChatModel("fallback result")
    llm = llm_provider.resilient_llm(primary=primary, fallback=fallback)
    assert await llm.ainvoke([]) == "fallback result"


def test_resilient_structured_llm_uses_primary_when_it_succeeds():
    primary = _FakeChatModel(_Schema(value="from primary"))
    fallback = _FakeChatModel(_Schema(value="from fallback"))
    structured = llm_provider.resilient_structured_llm(_Schema, primary=primary, fallback=fallback)
    assert structured.invoke([]).value == "from primary"


def test_resilient_structured_llm_falls_back_when_groq_fails():
    """planner.py's plan_goal/check_intent both use .with_structured_output
    -- this is the composition that has to survive a Groq outage too."""
    primary = _FakeChatModel(ConnectionError("groq unreachable"))
    fallback = _FakeChatModel(_Schema(value="from fallback"))
    structured = llm_provider.resilient_structured_llm(_Schema, primary=primary, fallback=fallback)
    assert structured.invoke([]).value == "from fallback"


def test_resilient_llm_raises_when_both_primary_and_fallback_fail():
    """No LLM available at all (Groq down + Ollama not running) is a real
    failure, not something to paper over -- it should still surface."""
    primary = _FakeChatModel(ConnectionError("groq unreachable"))
    fallback = _FakeChatModel(ConnectionError("ollama not running"))
    llm = llm_provider.resilient_llm(primary=primary, fallback=fallback)
    with pytest.raises(RuntimeError):
        llm.invoke([])


def test_resilient_llm_falls_back_to_real_ollama_live():
    """Closes the loop with real objects, not fakes: a real ChatGroq
    pointed at an unreachable endpoint (deterministic failure, doesn't
    depend on Groq's actual uptime) falls back to a real local Ollama
    call and gets a genuine response back. Needs `ollama serve` running
    locally with the configured model pulled -- a manual check if
    this one fails, same as the project's other `_live` tests."""
    from langchain_groq import ChatGroq

    primary = ChatGroq(
        model="openai/gpt-oss-120b",
        api_key="fake-key",
        groq_api_base="http://127.0.0.1:1/",  # nothing listens here -> immediate connection failure
        temperature=0,
    )
    llm = llm_provider.resilient_llm(primary=primary)
    response = llm.invoke("Reply with exactly the word: PONG")
    assert "PONG" in response.content


# ---------------------------------------------------------------------------
# is_ollama_reachable(): backs the Settings page's live fallback status
# (added after the page read as static info, not real state).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_ollama_reachable_returns_false_when_nothing_listens(monkeypatch):
    """Deterministic, no real Ollama dependency: port 1 on loopback refuses
    the connection instantly. Proves the failure path returns False
    cleanly rather than raising -- this is what CI (no local Ollama) and
    an actually-down Ollama both look like."""
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "ollama_base_url", "http://127.0.0.1:1", raising=False)
    assert await llm_provider.is_ollama_reachable(timeout=1.0) is False


@pytest.mark.asyncio
async def test_is_ollama_reachable_returns_true_live():
    """Live check against the real local Ollama server running on the dev
    machine (confirmed via `ollama.exe` / GET /api/tags). A manual
    check if this fails elsewhere; the
    deterministic-False test above covers the no-Ollama case."""
    assert await llm_provider.is_ollama_reachable() is True
