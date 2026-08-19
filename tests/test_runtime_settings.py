"""The Settings page's few genuinely-editable knobs
(LLM provider preference, log level, web search result count) -- added
so the page has real controls, not just an info view.
"""
from __future__ import annotations

import logging

import pytest

from src.runtime_settings import (
    effective_log_level,
    get_runtime_settings,
    reset_runtime_settings,
    update_runtime_settings,
)


def test_defaults_match_config_at_reset():
    reset_runtime_settings()
    runtime = get_runtime_settings()
    assert runtime.llm_provider_preference == "groq_first"
    assert runtime.web_search_max_results == 5
    assert effective_log_level() == "INFO"  # config.py's default


def test_update_llm_provider_preference():
    update_runtime_settings(llm_provider_preference="ollama_first")
    assert get_runtime_settings().llm_provider_preference == "ollama_first"


def test_update_rejects_an_unknown_provider_preference():
    with pytest.raises(ValueError):
        update_runtime_settings(llm_provider_preference="bing_first")


def test_update_log_level_takes_effect_immediately():
    """Not just stored -- the actual logger's level changes, verified by
    reading it back off logging itself rather than a separately-tracked
    string that could drift."""
    update_runtime_settings(log_level="DEBUG")
    assert effective_log_level() == "DEBUG"
    assert logging.getLogger("taskpilot").isEnabledFor(logging.DEBUG)


def test_update_rejects_an_unknown_log_level():
    with pytest.raises(ValueError):
        update_runtime_settings(log_level="SUPER_VERBOSE")


def test_update_web_search_max_results():
    update_runtime_settings(web_search_max_results=8)
    assert get_runtime_settings().web_search_max_results == 8


@pytest.mark.parametrize("bad_value", [0, -1, 21, 100])
def test_update_rejects_an_out_of_range_result_count(bad_value):
    with pytest.raises(ValueError):
        update_runtime_settings(web_search_max_results=bad_value)


def test_update_only_touches_fields_that_were_given():
    update_runtime_settings(web_search_max_results=3)
    update_runtime_settings(llm_provider_preference="ollama_first")
    runtime = get_runtime_settings()
    assert runtime.web_search_max_results == 3  # untouched by the second call
    assert runtime.llm_provider_preference == "ollama_first"


def test_reset_restores_defaults_and_log_level():
    update_runtime_settings(
        llm_provider_preference="ollama_first", log_level="DEBUG", web_search_max_results=1
    )
    reset_runtime_settings()
    runtime = get_runtime_settings()
    assert runtime.llm_provider_preference == "groq_first"
    assert runtime.web_search_max_results == 5
    assert effective_log_level() == "INFO"
