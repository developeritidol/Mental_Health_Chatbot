from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


# ─────────────────────────────────────────────────────────────────────────────
# ROLE ENUM
# Used in /register route to validate and normalize the role query parameter.
# FastAPI automatically rejects any value not in this enum with a 422 error.
# ─────────────────────────────────────────────────────────────────────────────

class UserRole(str, Enum):
    user  = "user"
    admin = "admin"


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN-ONLY ENUMS
# These are only required when role == "admin" during registration.
# ─────────────────────────────────────────────────────────────────────────────

class ProfessionalRole(str, Enum):
    PSYCHOLOGIST = "Licensed Psychologist (PhD / PsyD)"
    PSYCHIATRIST = "Psychiatrist (MD / DO)"
    LCSW         = "Licensed Clinical Social Worker (LCSW)"
    LPC          = "Licensed Professional Counselor (LPC)"
    LMFT         = "Marriage & Family Therapist (LMFT)"
    BCBA         = "Board Certified Behavior Analyst (BCBA)"
    OTHER        = "Other (with specification)"


class PracticeType(str, Enum):
    PRIVATE    = "Private"
    CLINIC     = "Clinic"
    TELEHEALTH = "Telehealth"


class ConsultationMode(str, Enum):
    IN_PERSON  = "In-person"
    TELEHEALTH = "Telehealth"


# ─────────────────────────────────────────────────────────────────────────────
# ASSESSMENT SCHEMAS
# Used by the onboarding flow (POST /api/assessment).
# ─────────────────────────────────────────────────────────────────────────────

class ProfileInput(BaseModel):
    """Basic user profile collected during onboarding."""
    first_name:                 str            = Field(...,        min_length=1, max_length=40)
    last_name:                  Optional[str]  = Field(None,       max_length=40)
    username:                   Optional[str]  = Field(None,       max_length=40)
    gender:                     Optional[str]  = None
    age:                        Optional[int]  = Field(None,       ge=1, le=120)
    emergency_contact_name:     Optional[str]  = None
    emergency_contact_relation: Optional[str]  = None
    emergency_contact_phone:    Optional[str]  = None


class PersonalityAnswers(BaseModel):
    """
    5 static personality questions answered during onboarding.
    Allowed values: 'Yes' / 'No' / 'Sometimes'
    """
    prefers_solitude:     str = "Sometimes"
    logic_over_emotion:   str = "Sometimes"
    plans_ahead:          str = "Sometimes"
    energized_by_social:  str = "Sometimes"
    trusts_instincts:     str = "Sometimes"


class AssessmentRequest(BaseModel):
    """
    Full onboarding payload sent from the Android app.
    POST /api/assessment
    """
    device_id:            str                = Field(..., min_length=1)
    profile:              ProfileInput
    personality_answers:  PersonalityAnswers


# ─────────────────────────────────────────────────────────────────────────────
# CHAT SCHEMA
# Used for streaming chat messages from the Android app.
# ─────────────────────────────────────────────────────────────────────────────

class StreamChatRequest(BaseModel):
    """
    Chat message payload from the Android app.
    POST /api/chat/stream
    """
    device_id: str = Field(..., min_length=1)
    message:   str = Field(..., min_length=1, max_length=2000)