"""
Application Configuration
──────────────────────────
Loaded once via lru_cache. All settings pulled from .env file.

v2 changes:
  • MAX_HISTORY_TURNS reduced from 20 → 15
    (20 turns = ~6000 tokens of history alone, pushing context near limit)
  • MAX_TOKENS changed from 2000 → 300 (soft default only)
    NOTE: llm.py now overrides max_tokens dynamically per message class.
    This value is only used as a safety ceiling for any call that bypasses
    the message classifier (e.g. direct calls in tests).
  • Added SYNTHESIZER_MODEL as a separate config key so it can be
    swapped independently from the main generator model.
"""
from pydantic_settings import BaseSettings
from functools import lru_cache
from app.core.logger import get_logger

logger = get_logger(__name__)


class Settings(BaseSettings):
    # ── App ───────────────────────────────────────────────────────────────────
    APP_NAME:    str = "MindBridge"
    APP_VERSION: str = "1.0.0"
    DEBUG:       bool = False

    # ── Groq — Main Generator ─────────────────────────────────────────────────
    GROQ_API_KEY: str  = ""
    GROQ_MODEL:   str  = "openai/gpt-oss-120b"   # Primary generator (highest quality on Groq)
    # Fallback if 120B is unavailable or too slow:
    # GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # ── Groq — Synthesizer (Llama-8B — fast, cheap, JSON output) ─────────────
    SYNTHESIZER_MODEL: str = "llama-3.1-8b-instant"

    # ── Groq Whisper (STT) ───────────────────────────────────────────────────
    GROQ_WHISPER_MODEL: str = "whisper-large-v3"

    # ── HuggingFace Emotion Model ─────────────────────────────────────────────
    # 28-label GoEmotions — runs locally via transformers pipeline
    HF_EMOTION_MODEL: str = "SamLowe/roberta-base-go_emotions"
    HF_API_TOKEN:     str = ""   # only needed for HF Inference API fallback

    # ── Session / History ─────────────────────────────────────────────────────
    # 15 turns = ~30 messages = sufficient for full emotional arc
    # 20 turns consumed too many context tokens with long system prompts
    MAX_HISTORY_TURNS: int = 15

    # ── Token ceiling ─────────────────────────────────────────────────────────
    # llm.py overrides this dynamically per message class.
    # This value is a safety fallback ONLY — never used for normal chat flow.
    MAX_TOKENS: int = 300

    class Config:
        env_file          = ".env"
        env_file_encoding = "utf-8"
        extra             = "ignore"


@lru_cache()
def get_settings() -> Settings:
    logger.debug("Loading application settings")
    return Settings()