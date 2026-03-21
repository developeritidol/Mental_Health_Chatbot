"""
Safety Service
───────────────
Lightweight crisis signal detection running in parallel with the LLM call.
Does NOT interrupt the conversation — the LLM handles that naturally.
This service is purely for backend logging, monitoring, and future escalation.
"""
from __future__ import annotations
from app.core.constants import CRISIS_SIGNAL_PHRASES
from app.core.logger import get_logger

logger = get_logger(__name__)


def check_crisis_signals(text: str, session_id: str) -> dict:
    """
    Returns a dict with crisis detection results.
    Any detected signals are logged for specialist review.
    """
    text_lower = text.lower()
    triggered = [p for p in CRISIS_SIGNAL_PHRASES if p in text_lower]

    result = {
        "is_crisis": len(triggered) > 0,
        "triggered_phrases": triggered,
        "session_id": session_id,
    }

    if result["is_crisis"]:
        logger.warning(
            f"[SAFETY] Crisis signal detected in session {session_id}: {triggered}"
        )

    return result


def check_emotion_trend(emotion_history: list[float]) -> dict:
    """
    Checks if sadness/hopelessness scores have been increasing over last 3 turns.
    `emotion_history` = list of sadness scores, most recent last.
    Returns trend information for dashboard monitoring.
    """
    if len(emotion_history) < 3:
        return {"trending_down": False, "severity": "low"}

    recent = emotion_history[-3:]
    is_worsening = recent[0] < recent[1] < recent[2]
    avg = sum(recent) / len(recent)

    severity = "high" if avg > 0.7 else "medium" if avg > 0.45 else "low"

    return {
        "trending_down": is_worsening,
        "severity": severity,
        "recent_scores": recent,
        "average": round(avg, 3),
    }