from pydantic import BaseModel, Field, EmailStr, model_validator
from typing import Optional
from enum import Enum


# ── Enums ─────────────────────────────────────────────────────────────────────

class GenderEnum(str, Enum):
    MALE = "male"
    FEMALE = "female"
    NON_BINARY = "non_binary"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"


class ProfessionalRole(str, Enum):
    PSYCHOLOGIST = "Licensed Psychologist (PhD / PsyD)"
    PSYCHIATRIST = "Psychiatrist (MD / DO)"
    LCSW = "Licensed Clinical Social Worker (LCSW)"
    LPC = "Licensed Professional Counselor (LPC)"
    LMFT = "Marriage & Family Therapist (LMFT)"
    BCBA = "Board Certified Behavior Analyst (BCBA)"
    OTHER = "Other (with specification)"
    NONE = "none"


class PracticeType(str, Enum):
    PRIVATE = "Private"
    CLINIC = "Clinic"
    TELEHEALTH = "Telehealth"
    NONE = "none"


class ConsultationMode(str, Enum):
    IN_PERSON = "In-person"
    TELEHEALTH = "Telehealth"
    NONE = "none"


# ── Assessment ────────────────────────────────────────────────────────────────

class ProfileInput(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=40)
    last_name: Optional[str] = Field(default=None, max_length=40)
    gender: Optional[str] = None
    age: Optional[int] = Field(default=None, ge=1, le=120)
    emergency_contact_name: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    emergency_contact_phone: Optional[str] = None


class PersonalityAnswers(BaseModel):
    prefers_solitude: str = "Sometimes"
    logic_over_emotion: str = "Sometimes"
    plans_ahead: str = "Sometimes"
    energized_by_social: str = "Sometimes"
    trusts_instincts: str = "Sometimes"


class AssessmentRequest(BaseModel):
    """POST /api/assessment — one-time onboarding from Android."""
    personality_answers: PersonalityAnswers


# ── Registration ──────────────────────────────────────────────────────────────

class UserCreateRequest(BaseModel):
    """
    FC3: is_admin removed — role determined solely by is_user.
    FC4: full_name replaced by first_name + last_name.
    FC5: gender and age added as required fields.
    FC6: model_validator enforces role-specific required fields.
    """
    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    phone_number: str = Field(..., pattern=r"^\+?[1-9]\d{1,14}$")
    # True = patient (users collection), False = counselor/admin (admins collection)
    is_user: bool = Field(..., description="True for patient, False for counselor/admin")
    gender: GenderEnum
    age: int = Field(..., ge=13, le=120)

    # Patient-specific fields
    emergency_contact_name: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    # Counselor-specific fields
    professional_role: Optional[str] = None
    license_number: Optional[str] = None
    state_of_licensure: Optional[str] = None
    npi_number: Optional[str] = None
    practice_type: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    consultation_mode: Optional[str] = None

    @model_validator(mode="after")
    def validate_role_fields(self) -> "UserCreateRequest":
        """FC6: enforce role-specific required fields at schema boundary."""
        if self.is_user:
            # Patient: emergency contact is required for crisis escalation
            missing = []
            if not self.emergency_contact_name:
                missing.append("emergency_contact_name")
            if not self.emergency_contact_relation:
                missing.append("emergency_contact_relation")
            if not self.emergency_contact_phone:
                missing.append("emergency_contact_phone")
            if missing:
                raise ValueError(
                    f"Patient registration requires: {', '.join(missing)}"
                )
        else:
            # Counselor: professional credentials are required for compliance
            required_counselor = [
                "professional_role", "license_number", "state_of_licensure",
                "npi_number", "practice_type", "city", "state", "consultation_mode",
            ]
            missing = [f for f in required_counselor if not getattr(self, f)]
            if missing:
                raise ValueError(
                    f"Counselor registration requires: {', '.join(missing)}"
                )
        return self

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "summary": "Patient registration",
                    "value": {
                        "first_name": "Jane",
                        "last_name": "Doe",
                        "email": "jane@example.com",
                        "password": "Abcd@1234",
                        "phone_number": "+911234567890",
                        "is_user": True,
                        "gender": "female",
                        "age": 28,
                        "emergency_contact_name": "John Doe",
                        "emergency_contact_relation": "Spouse",
                        "emergency_contact_phone": "+919876543210",
                    }
                },
                {
                    "summary": "Counselor registration",
                    "value": {
                        "first_name": "Dr. Sarah",
                        "last_name": "Smith",
                        "email": "sarah@clinic.com",
                        "password": "Abcd@1234",
                        "phone_number": "+911234567891",
                        "is_user": False,
                        "gender": "female",
                        "age": 42,
                        "professional_role": "Licensed Psychologist (PhD / PsyD)",
                        "license_number": "PSY12345",
                        "state_of_licensure": "California",
                        "npi_number": "1234567890",
                        "practice_type": "Private",
                        "city": "Los Angeles",
                        "state": "CA",
                        "consultation_mode": "In-person",
                        "emergency_contact_name": "John Doe",
                        "emergency_contact_relation": "Spouse",
                        "emergency_contact_phone": "+919876543210",
                    }
                }
            ]
        }
    }


# ── Auth ──────────────────────────────────────────────────────────────────────

class UserLoginRequest(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "jane@example.com",
                "password": "Abcd@1234"
            }
        }
    }


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

    model_config = {
        "json_schema_extra": {
            "example": {"email": "jane@example.com"}
        }
    }


class VerifyOtpRequest(BaseModel):
    email: str = Field(..., description="User email address")
    otp: str = Field(..., description="6-digit OTP")

    model_config = {
        "json_schema_extra": {
            "example": {"email": "jane@example.com", "otp": "123456"}
        }
    }


class ResetPasswordRequest(BaseModel):
    email: str = Field(..., description="User email address")
    new_password: str = Field(..., min_length=8, max_length=128)

    model_config = {
        "json_schema_extra": {
            "example": {"email": "jane@example.com", "new_password": "NewPass@1234"}
        }
    }


class RefreshTokenRequest(BaseModel):
    """POST /api/users/refresh — exchange a refresh token for a new access token."""
    refresh_token: str = Field(..., min_length=1)


# ── Chat ──────────────────────────────────────────────────────────────────────

class StreamChatRequest(BaseModel):
    """
    POST /api/chat/stream — every chat message from Android.
    FC7: user_id removed — identity is extracted exclusively from the JWT token.
    """
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=2000)
