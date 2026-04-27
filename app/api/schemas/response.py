from pydantic import BaseModel
from typing import Optional
from datetime import datetime


# ── Assessment response ───────────────────────────────────────

class AssessmentResponse(BaseModel):
    status: str
    session_id: str
    opening_message: str
    timestamp: datetime
    user_id: str


# ── Chat stream metadata ──────────────────────────────────────

class EmotionData(BaseModel):
    dominant_emotion: str
    response_mode: str
    message_class: str
    intensity: str
    recommended_tone: str
    is_crisis_signal: bool
    sadness_scores: list[float] = []


# ── Chat history API ──────────────────────────────────────────

class ChatMessageResponse(BaseModel):
    user_id: str
    role: str
    content: str
    timestamp: datetime


class ChatHistoryResponse(BaseModel):
    status: str
    user_id: str
    total_messages: int
    messages: list[ChatMessageResponse]


# ── Session list API ──────────────────────────────────────────

class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    is_active: bool
    is_escalated: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SessionListResponse(BaseModel):
    status: str
    user_id: str
    total_sessions: int
    sessions: list[SessionResponse]


# ── Human intervention API ────────────────────────────────────

class EscalatedSessionResponse(BaseModel):
    session_id: str
    user_id: str
    first_name: str = "Unknown"
    last_name: Optional[str] = None
    is_active: bool = True          # ← was silently dropped before (Issue #3)
    is_escalated: bool = True
    lethality_alert: bool = False
    escalated_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class EscalatedSessionListResponse(BaseModel):
    status: str
    total: int
    sessions: list[EscalatedSessionResponse]


# ── Health / utility ──────────────────────────────────────────

class TranscriptionResponse(BaseModel):
    text: str
    language: Optional[str] = None
    duration: Optional[float] = None


# ── Token / Auth ──────────────────────────────────────────────

class TokenData(BaseModel):
    user_id: str
    email: str
    role: Optional[str] = None


# ── User Profile ──────────────────────────────────────────────

class UserProfileData(BaseModel):
    user_id: str
    full_name: str
    email: str
    phone_number: str
    is_user: bool = True
    professional_role: Optional[str] = "str"
    license_number: Optional[str] = "str"
    state_of_licensure: Optional[str] = "str"
    npi_number: Optional[str] = "str"
    practice_type: Optional[str] = "str"
    city: Optional[str] = "str"
    state: Optional[str] = "str"
    consultation_mode: Optional[str] = "str"
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "user_id": "12345",
                "full_name": "John Doe",
                "email": "john@example.com",
                "phone_number": "+911234567890",
                "is_user": True,
                "professional_role": "Licensed Psychologist (PhD / PsyD)",
                "license_number": "LIC12345",
                "state_of_licensure": "California",
                "npi_number": "1234567890",
                "practice_type": "Private",
                "city": "Los Angeles",
                "state": "CA",
                "consultation_mode": "In-person"
            }
        }
    }


# ── Auth Responses ────────────────────────────────────────────

class UserSignupResponse(BaseModel):
    status: str
    message: str
    user_id: str


class UserLoginResponse(BaseModel):
    status: str
    message: str
    user: UserProfileData
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class ForgotPasswordResponse(BaseModel):
    status: str
    message: str


class VerifyOtpResponse(BaseModel):
    status: str
    message: str


class ResetPasswordResponse(BaseModel):
    status: str
    message: str


class RefreshTokenResponse(BaseModel):
    status: str
    access_token: str
    token_type: str = "bearer"


class LogoutResponse(BaseModel):
    status: str
    message: str


class UserProfileResponse(BaseModel):
    status: str
    user: UserProfileData