from pydantic import BaseModel
from typing import Optional


class EmotionData(BaseModel):
    dominant_emotion: str
    top_scores: dict[str, float]
    response_mode: str
    is_crisis_signal: bool


class ChatResponse(BaseModel):
    reply: str
    emotion: EmotionData
    session_id: str


class TranscriptionResponse(BaseModel):
    text: str
    language: Optional[str] = None
    duration: Optional[float] = None


class OpeningMessageResponse(BaseModel):
    message: str
    session_id: str