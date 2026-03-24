from pydantic import BaseModel
from typing import Optional


# ── Assessment response ───────────────────────────────────────────────────────

class AssessmentResponse(BaseModel):
    status: str
    session_id: str
    opening_message: str
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


# ── Health / utility ──────────────────────────────────────────────────────────

class TranscriptionResponse(BaseModel):
    text: str
    language: Optional[str] = None
    duration: Optional[float] = None