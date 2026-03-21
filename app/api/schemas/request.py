from pydantic import BaseModel, Field
from typing import Optional


class UserProfile(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    mood_score: int = Field(..., ge=1, le=10)
    topic: str = Field(..., min_length=1)
    country: str = Field(default="IN")   # ISO 2-letter code for crisis line selection


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(..., min_length=1, max_length=2000)
    profile: UserProfile
    history: list[dict] = Field(default_factory=list)
    # Accumulated sadness scores for trend detection
    sadness_scores: list[float] = Field(default_factory=list)


class AssessmentSubmit(BaseModel):
    name: str
    gender: Optional[str] = None
    age: Optional[int] = None
    profession: Optional[str] = None
    existing_conditions: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    mood_score: int = Field(..., ge=1, le=10)
    topic: str