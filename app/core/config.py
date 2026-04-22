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

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
from app.core.logger import get_logger

from dotenv import load_dotenv
load_dotenv()

logger = get_logger(__name__)


class Settings(BaseSettings):
    APP_NAME:    str = "MindBridge"
    APP_VERSION: str = "1.0.0"
    DEBUG:       bool = False
    
    # ── Database ──────────────────────────────────────────────────────────────
    MONGODB_URL: str = "mongodb://localhost:27017"
    DATABASE_NAME: str = "mindbridge_db"

    # ── OpenAI — Main Generator ───────────────────────────────────────────────
    OPENAI_API_KEY: str  = ""
    MAIN_MODEL: str = "gpt-4o"

    # ── OpenAI — Synthesizer (Fast JSON metadata) ────────────────────────────
    SYNTHESIZER_MODEL: str = "gpt-4o-mini"

    # ── Groq Whisper (STT) ───────────────────────────────────────────────────
    GROQ_API_KEY: str = ""
    GROQ_WHISPER_MODEL: str = "whisper-large-v3"

    # ── HuggingFace Emotion Model ─────────────────────────────────────────────
    # 28-label GoEmotions — runs locally via transformers pipeline
    HF_EMOTION_MODEL: str = "SamLowe/roberta-base-go_emotions"
    HF_API_TOKEN:     str = ""   # only needed for HF Inference API fallback

    # ── Session / History ─────────────────────────────────────────────────────
    # 50 turns = ~100 messages. GPT-4o has 128K context — use it.
    # This gives the AI full conversation memory, like ChatGPT.
    MAX_HISTORY_TURNS: int = 50

    # ── Token ceiling ─────────────────────────────────────────────────────────
    # llm.py overrides this dynamically per message class.
    # This value is a safety fallback ONLY — never used for normal chat flow.
    MAX_TOKENS: int = 300

    # ── Server address (used to build WebSocket URLs) ─────────────────────────
    SERVER_HOST: str = "localhost"
    SERVER_PORT: int = 8000

    # ── JWT Authentication ────────────────────────────────────────────────────
    SECRET_KEY: str = Field(
            ..., 
            min_length=32, 
            description="Must be set via SECRET_KEY environment variable"
        )    
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    class Config:
        env_file          = ".env"
        env_file_encoding = "utf-8"
        extra             = "ignore"

@lru_cache()
def get_settings() -> Settings:
    logger.debug("Loading application settings")
    return Settings()