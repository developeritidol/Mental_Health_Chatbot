from pydantic import BaseModel, Field, EmailStr
from typing import Optional
from enum import Enum

# 1. ENUMS (Define these first so they are ready for the classes below)
class UserRole(str, Enum):
    user = "user"
    admin = "admin"

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

# 2. SCHEMAS
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
    prefers_solitude: str = "Sometimes"
    logic_over_emotion: str = "Sometimes"
    plans_ahead: str = "Sometimes"
    energized_by_social: str = "Sometimes"
    trusts_instincts: str = "Sometimes"

class AssessmentRequest(BaseModel):
    device_id: str = Field(..., min_length=1)
    profile: ProfileInput
    personality_answers: PersonalityAnswers

class UserCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=3, max_length=100, pattern=r"^[a-zA-Z ]+$")
    username: str = Field(..., min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    phone_number: str = Field(..., pattern=r"^\+?[1-9]\d{1,14}$")
    role: UserRole = Field(default=UserRole.user)
    professional_role: Optional[ProfessionalRole] = None
    license_number: Optional[str] = Field(None, min_length=1, max_length=50)
    state_of_licensure: Optional[str] = Field(None, min_length=1, max_length=50)
    npi_number: Optional[str] = Field(None, max_length=10)
    practice_type: Optional[PracticeType] = None
    city: Optional[str] = Field(None, min_length=1, max_length=50)
    state: Optional[str] = Field(None, min_length=1, max_length=50)
    consultation_mode: Optional[ConsultationMode] = None

class UserLoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

class StreamChatRequest(BaseModel):
    device_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=2000)

class VerifyOtpRequest(BaseModel):
    email: str = Field(..., description="User email address")
    otp: str = Field(..., description="6-digit OTP")

class ResetPasswordRequest(BaseModel):
    email: str = Field(..., description="User email address")
    new_password: str = Field(..., min_length=4, max_length=128)

class RefreshTokenRequest(BaseModel):
    """POST /api/users/refresh - Exchange a refresh token for a new access token."""
    refresh_token: str = Field(..., min_length=1, description="A valid refresh token")