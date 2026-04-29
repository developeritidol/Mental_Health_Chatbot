"""
Clinical Handoff Summarization Service
─────────────────────────────────────────
Generates a comprehensive (600-700 word) clinical history and briefing for a counselor
who is about to join an active crisis session. Fetches:
  - The user's past conversational history (all prior sessions)
  - The current session's AI conversation (live context leading to the crisis)
Then calls GPT-4o to synthesise both into a structured handoff note.

This service is always called as an asyncio background task so the slow
LLM call never blocks the routing engine or the user-facing SSE stream.
"""

import logging
from openai import AsyncOpenAI
from app.core.config import get_settings
from app.core.database import get_database

logger = logging.getLogger(__name__)


async def generate_clinical_handoff(
    user_id: str,
    current_session_id: str,
    crisis_category: str,
    handoff_mode: str = "comprehensive_summary",
) -> str:
    """
    Returns a comprehensive 600-700 word clinical handoff note for the incoming counselor.
    Falls back to a minimal template string if the LLM call fails, so the
    counselor always receives *something* rather than an empty brief.
    """
    db = get_database()
    if db is None:
        return _fallback_summary(crisis_category)

    try:
        current_context_str = await _fetch_current_session_context(db, current_session_id)
        
        # Mode 1: First-Time User -> Skip LLM, send raw logs
        if handoff_mode == "raw_history":
            past_context_str = await _fetch_past_session_context(db, user_id)
            return (
                f"--- RAW SESSION LOGS ---\n"
                f"CRISIS CATEGORY: {crisis_category}\n\n"
                f"PAST HISTORY:\n{past_context_str}\n\n"
                f"CURRENT CRISIS TRIGGER:\n{current_context_str}"
            )

        # Mode 2: Returning to SAME Counselor -> Fast, short summary of current trigger
        if handoff_mode == "short_summary":
            system_prompt = (
                "You are a clinical handoff assistant. The incoming counselor ALREADY "
                "knows this patient. Write a fast, concise briefing (under 150 words) "
                "summarizing ONLY the immediate trigger in the current session."
            )
            user_prompt = (
                f"CRISIS CATEGORY: {crisis_category}\n\n"
                f"CURRENT SESSION (AI conversation leading to crisis):\n{current_context_str}\n\n"
                "Write the short briefing note now."
            )
            max_tokens = 250

        # Mode 3: Returning to NEW Counselor -> Comprehensive history summary
        else:
            past_context_str = await _fetch_past_session_context(db, user_id)
            system_prompt = (
                "You are a clinical handoff assistant for mental health professionals. "
                "Write a comprehensive clinical history and briefing (approximately 600-700 words) "
                "for a counselor who is about to join an active crisis session. Extract the most "
                "important information from the prior sessions to ensure continuity of care. "
                "Do not include any preamble or labels. Output only the briefing note."
            )
            user_prompt = (
                f"CRISIS CATEGORY: {crisis_category}\n\n"
                f"PRIOR SESSION HISTORY:\n{past_context_str}\n\n"
                f"CURRENT SESSION (AI conversation leading to crisis):\n{current_context_str}\n\n"
                "Write the comprehensive handoff note now."
            )
            max_tokens = 1500

        settings = get_settings()
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
        )

        summary = response.choices[0].message.content.strip()
        logger.info(f"[SUMMARIZATION] Handoff generated ({handoff_mode}) for session {current_session_id}")
        return summary

    except Exception as e:
        logger.exception(f"[SUMMARIZATION] LLM call failed for session {current_session_id}: {e}")
        return _fallback_summary(crisis_category)


# ── Private helpers ───────────────────────────────────────────────────────────

async def _fetch_past_session_context(db, user_id: str) -> str:
    """Returns a formatted transcript of all the user's past conversations (up to 150 messages)."""
    try:
        # Find all past sessions for the user
        cursor = db.sessions.find(
            {"user_id": user_id},
            sort=[("created_at", -1)],
        )
        past_sessions = await cursor.to_list(length=50) # last 50 sessions
        
        if not past_sessions:
            return "No prior session history found."
            
        session_ids = [s["session_id"] for s in past_sessions]
        
        # Fetch the most recent 150 messages across all those sessions
        msg_cursor = db.messages.find(
            {"session_id": {"$in": session_ids}},
            sort=[("timestamp", -1)],
        )
        messages = await msg_cursor.to_list(length=150)
        
        if not messages:
            return "No prior session history found."
            
        # Reverse to chronological order
        messages.reverse()
        return _format_messages(messages)
    except Exception as e:
        logger.error(f"[SUMMARIZATION] Past context fetch failed for user {user_id}: {e}")
        return "No prior session history found."


async def _fetch_current_session_context(db, session_id: str) -> str:
    """Returns a formatted transcript of the current AI conversation."""
    try:
        cursor = db.messages.find(
            {"session_id": session_id},
            sort=[("timestamp", 1)],
        )
        messages = await cursor.to_list(length=20)
        return _format_messages(messages) or "No messages in current session."
    except Exception as e:
        logger.error(f"[SUMMARIZATION] Current context fetch failed for session {session_id}: {e}")
        return "No messages in current session."


def _format_messages(messages: list) -> str:
    lines = []
    for msg in messages:
        sender = msg.get("role", msg.get("sender_type", "unknown")).upper()
        content = msg.get("content", "").strip()
        if content:
            lines.append(f"{sender}: {content}")
    return "\n".join(lines)


def _fallback_summary(crisis_category: str) -> str:
    return (
        f"Automated summary unavailable. Crisis category: {crisis_category}. "
        "Please review the session history manually before engaging with the user."
    )
