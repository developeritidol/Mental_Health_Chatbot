from pydantic import BaseModel
from typing import Optional
from datetime import datetime


# ── Assessment response ───────────────────────────────────────────────────────

class AssessmentResponse(BaseModel):
    status: str
    session_id: str
    opening_message: str
    timestamp: datetime
    device_id: str


# ── Chat stream metadata (sent as final SSE event) ───────────────────────────

class EmotionData(BaseModel):
    dominant_emotion: str
    response_mode: str
    message_class: str
    intensity: str
    recommended_tone: str
    is_crisis_signal: bool
    sadness_scores: list[float] = []


# ── Chat history API ─────────────────────────────────────────────────────────

class ChatMessageResponse(BaseModel):
    session_id: str
    role: str
    content: str
    timestamp: datetime


class ChatHistoryResponse(BaseModel):
    status: str
    device_id: str
    total_messages: int
    messages: list[ChatMessageResponse]


# ── Health / utility ──────────────────────────────────────────────────────────

class TranscriptionResponse(BaseModel):
    text: str
    language: Optional[str] = None
    duration: Optional[float] = None