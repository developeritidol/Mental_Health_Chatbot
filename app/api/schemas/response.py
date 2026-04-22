from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# ── Assessment response ───────────────────────────────────────────────────────

class AssessmentResponse(BaseModel):
    status: str
    session_id: str
    opening_message: str
    timestamp: datetime
    user_id: str


# ── Chat stream metadata (sent as final SSE event) ───────────────────────────

class EmotionData(BaseModel):
    dominant_emotion: str
    response_mode: str
    message_class: str
    intensity: str
    recommended_tone: str
    is_crisis_signal: bool
    sadness_scores: list[float] = []


# ── Chat history API ─────────────────────────────────────────────────────────

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


# ── Session list API ─────────────────────────────────────────────────────────

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


# ── Human intervention API ───────────────────────────────────────────────────

class EscalatedSessionResponse(BaseModel):
    session_id: str
    user_id: str
    first_name: str = "Unknown"
    last_name: Optional[str] = None
    username: Optional[str] = None
    is_escalated: bool
    escalated_at: Optional[str] = None
    created_at: Optional[str] = None


class EscalatedSessionListResponse(BaseModel):
    status: str
    total: int
    sessions: list[EscalatedSessionResponse]


# ── Health / utility ──────────────────────────────────────────────────────────

class TranscriptionResponse(BaseModel):
    text: str
    language: Optional[str] = None
    duration: Optional[float] = None


# ── Admin / User API responses ─────────────────────────────────────────────────

class TokenData(BaseModel):
<<<<<<< HEAD
    sub: str
    role: Optional[str] = None


class UserProfileData(BaseModel):
    """User profile data with consistent field order"""
    full_name: str
    username: str
    email: str
    phone_number: str
    role: str
    professional_role: Optional[str] = None
    license_number: Optional[str] = None
    state_of_licensure: Optional[str] = None
    npi_number: Optional[str] = None
    practice_type: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    consultation_mode: Optional[str] = None
    user_id: str
    is_active: bool = True
    last_login: Optional[datetime] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "full_name": "John Doe",
                "username": "John_Doe13",
                "email": "abcd@gmail.com",
                "phone_number": "+91 1234567890",
                "role": "user",
                "professional_role": None,
                "license_number": None,
                "state_of_licensure": None,
                "npi_number": None,
                "practice_type": None,
                "city": None,
                "state": None,
                "consultation_mode": None,
                "user_id": "user_12345",
                "is_active": True,
                "last_login": None
            }
        }
    }
=======
    useremail: str
    user_id: Optional[str] = None
>>>>>>> 1317e4c411bdef0b6b5dba31035af40b6db0bd5b


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
