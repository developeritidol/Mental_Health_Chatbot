"""
Application configuration loaded from environment variables via pydantic-settings.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    # -- Groq LLM --
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-120b"

    # -- HuggingFace Emotion Model --
    SENTIMENT_MODEL: str = "j-hartmann/emotion-english-distilroberta-base"

    # -- App --
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # -- LLM Parameters --
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 1000

    # -- Memory --
    MEMORY_WINDOW_SIZE: int = 20  # Keep last N messages (5 user + 5 assistant turns)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    """Returns a cached Settings instance."""
    return Settings()
