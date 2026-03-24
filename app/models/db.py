from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime


class EmergencyContact(BaseModel):
    name: Optional[str] = None
    relation: Optional[str] = None
    phone: Optional[str] = None


class UserModelDB(BaseModel):
    device_id: str
    name: str
    gender: Optional[str] = None
    age: Optional[int] = None
    emergency_contact: Optional[EmergencyContact] = None
    personality_answers: Optional[Dict[str, str]] = None   # raw answers from Android
    personality_summary: Optional[str] = None               # computed human-readable string
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_active: datetime = Field(default_factory=datetime.utcnow)


class SessionModelDB(BaseModel):
    session_id: str
    device_id: str
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
