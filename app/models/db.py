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
    email: Optional[str] = None
    password_hash: Optional[str] = None
    phone_number: Optional[str] = None
    is_user: bool = True
    is_admin: bool = False
    professional_role: Optional[str] = None  # e.g., "Licensed Psychologist (PhD / PsyD)"
    license_number: Optional[str] = None
    state_of_licensure: Optional[str] = None
    npi_number: Optional[str] = None
    practice_type: Optional[str] = None  # e.g., "Private"
    city: Optional[str] = None
    state: Optional[str] = None
    consultation_mode: Optional[str] = None  # e.g., "In-person"
    is_active: bool = True  # Account status: True=active, False=disabled/suspended
    last_login: Optional[datetime] = None
    password_reset_token: Optional[str] = None
    password_reset_expires: Optional[datetime] = None

    # ── Smart Routing ─────────────────────────────────────────────────────────
    preferred_counselor_id: Optional[str] = None   # _id of the last trusted counselor
    last_crisis_category: Optional[str] = None      # category from previous escalation

    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_active: datetime = Field(default_factory=datetime.utcnow)


class AdminModelDB(BaseModel):
    user_id: str = ""
    full_name: str
    email: str
    password_hash: str
    phone_number: str
    role: str = "admin"
    professional_role: str
    license_number: str
    state_of_licensure: str
    npi_number: str
    practice_type: str
    city: str
    state: str
    consultation_mode: str
    # ── Smart Routing — Presence & Capacity ──────────────────────────────────
    is_online: bool = False
    current_active_sessions: int = 0
    max_concurrent_sessions: int = 3
    last_ping: Optional[datetime] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None


class SessionModelDB(BaseModel):
    session_id: str
    user_id: str = ""
    doctor_id: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    status: str = "active"
    summary: Optional[str] = None

    # ── Smart Routing ─────────────────────────────────────────────────────────
    assigned_counselor_id: Optional[str] = None    # _id string of the routed counselor
    crisis_category: Optional[str] = None           # Groq consensus category at escalation time
    handoff_summary: Optional[str] = None           # LLM-generated clinical brief for the counselor


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
    sender_type: str = "user"  # "user" or "doctor" or "assistant"
    sender_id: str = ""
    turn_number: int = 1
    role: str
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    roberta_analysis: Optional[RobertaAnalysis] = None
    llm_consensus: Optional[LLMConsensus] = None
    tokens_used: Optional[int] = None
