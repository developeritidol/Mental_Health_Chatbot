"""
LangGraph state definition for the Mental Health Chatbot.
This TypedDict flows through every node in the graph.
"""

from typing import TypedDict, List, Dict, Optional
from langchain_core.messages import AnyMessage


class ChatState(TypedDict):
    """State object that flows through the LangGraph pipeline."""

    # -- Input fields (populated by the FastAPI route) --
    user_message: str                        # Current user message
    messages: List[AnyMessage]               # Conversation history (last N turns)
    assessment: Dict[str, str]               # Baseline assessment results

    # -- Populated by Emotion Node --
    current_emotion: Dict[str, float]        # e.g. {"sadness": 0.94, "fear": 0.12}
    top_emotion_label: str                   # e.g. "sadness"
    top_emotion_score: float                 # e.g. 0.94

    # -- Populated by Guardrail Node --
    risk_level: str                          # "LOW", "MEDIUM", "HIGH"
    is_crisis: bool                          # True if immediate crisis detected
    crisis_response: Optional[str]           # Pre-built crisis response (if is_crisis)

    # -- Populated by LLM Node --
    intent: str                              # Classified mental health intent
    llm_response: str                        # Final empathetic response
