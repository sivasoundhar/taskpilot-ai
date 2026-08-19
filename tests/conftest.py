"""Shared pytest fixtures."""
import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_history_db(tmp_path, monkeypatch):
    """Every test gets its own throwaway SQLite file for run history.

    Without this, any test that drives POST /run to a real completion
    (several already do, live end-to-end) would call storage.save_run()
    against whatever history_db_path get_settings() resolves to by
    default -- i.e. the real dev data/history.db. autouse=True so this
    applies session-wide without every test file needing to remember it.
    """
    import src.storage as storage_module

    monkeypatch.setattr(storage_module, "_db_path", lambda: tmp_path / "test_history.db")


@pytest.fixture(autouse=True)
def _reset_runtime_settings():
    """The Settings page's live-editable knobs (LLM provider preference,
    log level, search result count -- src/runtime_settings.py) are a
    single process-wide singleton. Without this, a test that flips one
    (or a real request during manual testing, since it's the same
    process) would leak into whichever test runs next."""
    from src.runtime_settings import reset_runtime_settings

    reset_runtime_settings()
    yield
    reset_runtime_settings()


@pytest.fixture(autouse=True)
def _disable_tavily_by_default(monkeypatch):
    """Most tests predate Tavily and expect the original DuckDuckGo-only
    behavior. A real dev .env may have a live TAVILY_API_KEY -- without this,
    every one of those tests would silently start hitting Tavily's real
    API first. Tavily-specific tests re-enable it explicitly by
    monkeypatching tavily_api_key back on within that test."""
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "tavily_api_key", "", raising=False)
