from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime


class EmergencyContact(BaseModel):
    name: Optional[str] = None
    relation: Optional[str] = None
    phone: Optional[str] = None


class UserModelDB(BaseModel):
    user_id: Optional[str] = None
    name: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    emergency_contact: Optional[EmergencyContact] = None
    personality_answers: Optional[Dict[str, str]] = None   # raw answers from Android
    personality_summary: Optional[str] = None               # computed human-readable string

    # ── Authentication / admin fields ─────────────────────────────────────────
    full_name: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    password_hash: Optional[str] = None
    phone_number: Optional[str] = None
    professional_role: Optional[str] = None  # e.g., "Licensed Psychologist (PhD / PsyD)"
    license_number: Optional[str] = None
    state_of_licensure: Optional[str] = None
    npi_number: Optional[str] = None
    practice_type: Optional[str] = None  # e.g., "Private"
    city: Optional[str] = None
    state: Optional[str] = None
    consultation_mode: Optional[str] = None  # e.g., "In-person"
    role: str = "user"
    is_active: bool = True  # Account status: True=active, False=disabled/suspended
    last_login: Optional[datetime] = None
    password_reset_token: Optional[str] = None
    password_reset_expires: Optional[datetime] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_active: datetime = Field(default_factory=datetime.utcnow)


class SessionModelDB(BaseModel):
    session_id: str
    user_id: str
    is_active: bool = True
    lethality_alert: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RobertaAnalysis(BaseModel):
    dominant_emotion: str
    scores: Dict[str, float] = Field(default_factory=dict)


class LLMConsensus(BaseModel):
    category: str
    intensity: str
    message_class: str
    is_crisis: bool
    recommended_tone: str
    reasoning: str


class MessageModelDB(BaseModel):
    session_id: str
    turn_number: int
    role: str
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    roberta_analysis: Optional[RobertaAnalysis] = None
    llm_consensus: Optional[LLMConsensus] = None
    tokens_used: Optional[int] = None
