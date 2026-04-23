from pydantic import BaseModel, Field, EmailStr, validator
from typing import Optional
from enum import Enum

# 1. ENUMS (Define these first so they are ready for the classes below)
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

# 2. SCHEMAS
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

class UserCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=3, max_length=100, pattern=r"^[a-zA-Z ]+$")
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    phone_number: str = Field(..., pattern=r"^\+?[1-9]\d{1,14}$")
    is_user: bool = Field(..., description="Required boolean field")
    is_admin: bool = Field(..., description="Required boolean field")
    professional_role: Optional[str] = "str"
    license_number: Optional[str] = "str"
    state_of_licensure: Optional[str] = "str"
    npi_number: Optional[str] = "str"
    practice_type: Optional[str] = "str"
    city: Optional[str] = "str"
    state: Optional[str] = "str"
    consultation_mode: Optional[str] = "str"

    @validator('is_user')
    def validate_user_role(cls, v, values):
        # If both roles are false, default is_user to true
        if 'is_admin' in values and not values['is_admin'] and not v:
            return True
        # Prevent both roles from being true (business logic)
        if 'is_admin' in values and values['is_admin'] and v:
            raise ValueError('User cannot be both admin and regular user simultaneously')
        return v

    class Config:
        schema_extra = {
            "example": {
                "full_name": "John Doe",
                "email": "abc@gmail.com",
                "password": "Abcd@1234",
                "phone_number": "+911234567890",
                "is_user": True,
                "is_admin": False,
                "professional_role": None,
                "license_number": None,
                "state_of_licensure": None,
                "npi_number": None,
                "practice_type": None,
                "city": None,
                "state": None,
                "consultation_mode": None
            }
        }
class UserLoginRequest(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "john@example.com",
                "password": "Abcd@1234"
            }
        }
    }

class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "abcd@gmail.com"
            }
        }
    }

class StreamChatRequest(BaseModel):
    """POST /api/chat/stream — every chat message from Android."""
    user_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=2000)

class VerifyOtpRequest(BaseModel):
    email: str = Field(..., description="User email address")
    otp: str = Field(..., description="6-digit OTP")

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "abcd@gmail.com",
                "otp": "123456"
            }
        }
    }

class ResetPasswordRequest(BaseModel):
    email: str = Field(..., description="User email address")
    new_password: str = Field(..., min_length=8, max_length=128)

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "abcd@gmail.com",
                "new_password": "Abcd@1234"
            }
        }
    }

class RefreshTokenRequest(BaseModel):
    """POST /api/users/refresh - Exchange a refresh token for a new access token."""
    refresh_token: str = Field(..., min_length=1, description="A valid refresh token")
