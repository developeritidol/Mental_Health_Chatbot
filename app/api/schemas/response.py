"""
Pydantic response models for the Mental Health Chatbot API.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict


class EmotionResult(BaseModel):
    """Emotion analysis result from the HuggingFace model."""
    label: str = Field(..., description="Detected emotion (e.g., sadness, fear, joy)")
    score: float = Field(..., description="Confidence score (0.0 - 1.0)")


class CrisisResource(BaseModel):
    """A crisis resource with contact information."""
    name: str
    contact: str
    description: str


class ChatResponse(BaseModel):
    """Response body for the /api/chat endpoint (non-streaming fallback)."""
    emotion: EmotionResult
    intent: Optional[str] = Field(None, description="Classified mental health intent")
    risk_level: str = Field(..., description="LOW, MEDIUM, or HIGH")
    response: str = Field(..., description="The chatbot's empathetic response")
    crisis_resources: Optional[List[CrisisResource]] = None
    session_id: Optional[str] = None


class AnalyzeResponse(BaseModel):
    """Response body for the /api/analyze endpoint."""
    emotion: EmotionResult
    intent: Optional[str] = None
    risk_level: str


class AssessmentResponse(BaseModel):
    """Response body for the /api/assessment/questions endpoint."""
    questions: List[Dict]


class HealthResponse(BaseModel):
    """Response body for the /health endpoint."""
    status: str = "ok"
    version: str = "1.0.0"
