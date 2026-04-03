from pydantic import BaseModel, Field
from typing import Optional


# ── Assessment API schemas ────────────────────────────────────────────────────

class ProfileInput(BaseModel):
    """Profile data collected during onboarding."""
    first_name: str = Field(..., min_length=1, max_length=40)
    last_name: Optional[str] = Field(default=None, max_length=40)
    username: Optional[str] = Field(default=None, max_length=40)
    gender: Optional[str] = None
    age: Optional[int] = Field(default=None, ge=1, le=120)
    emergency_contact_name: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    emergency_contact_phone: Optional[str] = None


class PersonalityAnswers(BaseModel):
    """5 static personality questions. Values: 'Yes' / 'No' / 'Sometimes'."""
    prefers_solitude: str = "Sometimes"
    logic_over_emotion: str = "Sometimes"
    plans_ahead: str = "Sometimes"
    energized_by_social: str = "Sometimes"
    trusts_instincts: str = "Sometimes"


class AssessmentRequest(BaseModel):
    """POST /api/assessment — one-time onboarding from Android."""
    device_id: str = Field(..., min_length=1)
    profile: ProfileInput
    personality_answers: PersonalityAnswers


# ── Chat API schemas ─────────────────────────────────────────────────────────

class StreamChatRequest(BaseModel):
    """POST /api/chat/stream — every chat message from Android."""
    device_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=2000)