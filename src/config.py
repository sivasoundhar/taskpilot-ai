"""Type-safe app configuration, loaded from environment / .env.

Everything the app needs to know at startup lives here — no bare
os.getenv() calls scattered around the codebase. Pydantic Settings
validates types and gives clear errors on misconfiguration.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # --- LLM: Groq (primary) ---
    groq_api_key: str = ""
    # llama-3.3-70b-versatile was deprecated by Groq (announced 2026-06-17)
    # and stopped working entirely without warning. This is Groq's own
    # recommended replacement.
    groq_model: str = "openai/gpt-oss-120b"

    # --- LLM: Ollama (fallback) ---
    # Used automatically when Groq's call fails -- see src/agent/llm_provider.py.
    # No API key needed; just needs `ollama serve` running locally with this
    # model pulled (`ollama pull llama3.2`).
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"

    # --- File System tool sandbox ---
    files_sandbox_dir: str = "./workspace"

    # --- Run history (Tasks/History nav pages) ---
    # Deliberately separate from files_sandbox_dir: that directory is
    # writable by the agent itself (file_system tool); this one is our
    # own app data and shouldn't be reachable from agent-written code.
    history_db_path: str = "./data/history.db"

    # --- Web Search: Tavily (primary if configured), DuckDuckGo MCP (fallback) ---
    # Tavily is purpose-built for LLM/agent search (structured results, no
    # HTML-scraping fragility) but needs a key; DuckDuckGo needs
    # none. See src/tools/web_search.py.
    tavily_api_key: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS_ORIGINS is a comma-separated string in .env; FastAPI wants a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached so we parse the environment once, not on every request."""
    return Settings()
