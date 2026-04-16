from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


# ── Admin Enums ───────────────────────────────────────────────────────────────
class ProfessionalRole(str, Enum):
    PSYCHOLOGIST = "Licensed Psychologist (PhD / PsyD)"
    PSYCHIATRIST = "Psychiatrist (MD / DO)"
    LCSW = "Licensed Clinical Social Worker (LCSW)"
    LPC = "Licensed Professional Counselor (LPC)"
    LMFT = "Marriage & Family Therapist (LMFT)"
    BCBA = "Board Certified Behavior Analyst (BCBA)"
    OTHER = "Other (with specification)"


class PracticeType(str, Enum):
    PRIVATE = "Private"
    CLINIC = "Clinic"
    TELEHEALTH = "Telehealth"


class ConsultationMode(str, Enum):
    IN_PERSON = "In-person"
    TELEHEALTH = "Telehealth"


# ── Assessment Schemas ───────────────────────────────────────────────────────
class ProfileInput(BaseModel):
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


class LoginRequest(BaseModel):
    """Simple login request with only username and password."""
    username: str = Field(..., min_length=1, description="Email or phone number")
    password: str = Field(..., min_length=8, description="User password")


# ── Chat API schemas ─────────────────────────────────────────────────────────

class StreamChatRequest(BaseModel):
    """POST /api/chat/stream — every chat message from Android."""
    device_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=2000)


# ── Admin Schemas ────────────────────────────────────────────────────────────
class AdminSignupRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(
        ..., 
        pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
        description="Valid email address"
    )
    password: str = Field(..., min_length=8, max_length=128)
    phone_number: str = Field(
        ..., 
        pattern=r"^\+?[1-9]\d{1,14}$",
        description="International phone number"
    )
    professional_role: ProfessionalRole = Field(...)
    license_number: str = Field(..., min_length=1, max_length=50)
    state_of_licensure: str = Field(..., min_length=1, max_length=50)
    npi_number: str = Field(..., min_length=10, max_length=10)
    practice_type: PracticeType = Field(...)
    city: str = Field(..., min_length=1, max_length=50)
    state: str = Field(..., min_length=1, max_length=50)
    consultation_mode: ConsultationMode = Field(...)

class VerifyOtpRequest(BaseModel):
    email: str = Field(..., description="User email address")
    otp: str = Field(..., min_length=6, max_length=6, description="6-digit OTP")