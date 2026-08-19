"""In-memory, live-editable settings -- deliberately separate from
config.py's Settings (env-file-backed, loaded once, needs a restart to
change). These are the few knobs safe to flip from the Settings page
without a restart or any hot-reload machinery: which LLM to try first,
the log level, and how many results a web search returns.

Resets to config.py's defaults on every backend restart -- intentional,
not a bug: this is live session tuning, not persisted configuration. If
it needs to survive a restart later, that's a real design question
(where does it persist? per-user? -- see SettingsPage.tsx's original
"bigger design question" note) worth its own decision, not a silent
side effect of this file.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from src.config import get_settings

ProviderPreference = Literal["groq_first", "ollama_first"]

_VALID_PREFERENCES = ("groq_first", "ollama_first")
_MIN_SEARCH_RESULTS = 1
_MAX_SEARCH_RESULTS = 20


@dataclass
class RuntimeSettings:
    llm_provider_preference: ProviderPreference = "groq_first"
    web_search_max_results: int = 5


_runtime = RuntimeSettings()


def get_runtime_settings() -> RuntimeSettings:
    return _runtime


def effective_log_level() -> str:
    """The logger's actual current level -- reflects a runtime override if
    one was ever applied, config.py's startup default otherwise. Reading
    it back from the logger itself (not a separate stored string) means
    this can never drift from what logging is really doing."""
    return logging.getLevelName(logging.getLogger("taskpilot").getEffectiveLevel())


def _level_name_to_numeric(name: str) -> int:
    numeric = logging.getLevelName(name.upper())
    if not isinstance(numeric, int):
        raise ValueError(f"Unknown log level: {name!r}")
    return numeric


def update_runtime_settings(
    *,
    llm_provider_preference: str | None = None,
    log_level: str | None = None,
    web_search_max_results: int | None = None,
) -> RuntimeSettings:
    """Validates and applies whichever fields are given; leaves the rest
    untouched. Raises ValueError on an invalid value -- the caller (the
    POST /settings endpoint) turns that into a 400, never a silent no-op
    or a crash."""
    if llm_provider_preference is not None:
        if llm_provider_preference not in _VALID_PREFERENCES:
            raise ValueError(f"llm_provider_preference must be one of {_VALID_PREFERENCES}")
        _runtime.llm_provider_preference = llm_provider_preference  # type: ignore[assignment]

    if log_level is not None:
        # Takes effect immediately -- the very next log call uses it, no
        # restart needed. This is what makes the Settings page's log-level
        # control a real, live change rather than a label that does nothing.
        logging.getLogger("taskpilot").setLevel(_level_name_to_numeric(log_level))

    if web_search_max_results is not None:
        if not (_MIN_SEARCH_RESULTS <= web_search_max_results <= _MAX_SEARCH_RESULTS):
            raise ValueError(
                f"web_search_max_results must be between {_MIN_SEARCH_RESULTS} and {_MAX_SEARCH_RESULTS}"
            )
        _runtime.web_search_max_results = web_search_max_results

    return _runtime


def reset_runtime_settings() -> None:
    """Restores every runtime-tunable knob to config.py's defaults.
    tests/conftest.py's autouse fixture calls this before each test so a
    test that flips the LLM preference or log level can't leak into the
    next one."""
    global _runtime
    _runtime = RuntimeSettings()
    logging.getLogger("taskpilot").setLevel(_level_name_to_numeric(get_settings().log_level))
