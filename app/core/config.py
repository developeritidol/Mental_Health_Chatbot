from pydantic_settings import BaseSettings
from functools import lru_cache
from app.core.logger import get_logger

logger = get_logger(__name__)


class Settings(BaseSettings):
    # App
    APP_NAME: str = "MindBridge"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # Groq LLM
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"   # Best quality on Groq

    # Groq Whisper (STT)
    GROQ_WHISPER_MODEL: str = "whisper-large-v3"

    # HuggingFace Emotion Model (runs locally via transformers)
    # 28-label GoEmotions — far richer than the 7-label alternative
    HF_EMOTION_MODEL: str = "SamLowe/roberta-base-go_emotions"
    HF_API_TOKEN: str = ""        # only needed if using HF Inference API fallback

    # Session
    MAX_HISTORY_TURNS: int = 20   # last N turns kept in memory
    MAX_TOKENS: int = 2000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    logger.debug("Loading application settings")
    return Settings()