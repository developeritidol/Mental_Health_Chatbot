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
    session_id: str
    total_messages: int
    messages: list[ChatMessageResponse]


# ── Session list API ─────────────────────────────────────────────────────────

class SessionResponse(BaseModel):
    session_id: str
    device_id: str
    is_active: bool
    is_escalated: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SessionListResponse(BaseModel):
    status: str
    device_id: str
    total_sessions: int
    sessions: list[SessionResponse]


# ── Human intervention API ───────────────────────────────────────────────────

class EscalatedSessionResponse(BaseModel):
    session_id: str
    device_id: str
    is_escalated: bool
    escalated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class EscalatedSessionListResponse(BaseModel):
    status: str
    total: int
    sessions: list[EscalatedSessionResponse]


# ── Health / utility ──────────────────────────────────────────────────────────

class TranscriptionResponse(BaseModel):
    text: str
    language: Optional[str] = None
    duration: Optional[float] = None