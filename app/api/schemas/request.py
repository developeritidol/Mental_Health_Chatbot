"""
Pydantic request models for the Mental Health Chatbot API.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict


class MessageTurn(BaseModel):
    """A single message turn in the conversation history."""
    role: str = Field(..., description="Either 'user' or 'assistant'")
    content: str = Field(..., description="The message content")


class ChatRequest(BaseModel):
    """Request body for the /api/chat/stream endpoint."""
    message: str = Field(..., min_length=1, description="The user's current message")
    session_id: Optional[str] = Field(None, description="Optional session identifier")
    assessment_results: Optional[Dict[str, str]] = Field(
        None, description="Results from the initial assessment questionnaire"
    )
    conversation_history: Optional[List[MessageTurn]] = Field(
        default_factory=list, description="Previous conversation turns (last N messages)"
    )


class AnalyzeRequest(BaseModel):
    """Request body for the /api/analyze endpoint."""
    message: str = Field(..., min_length=1, description="Text to analyze")
