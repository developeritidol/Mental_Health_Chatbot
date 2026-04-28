"""
Clinical Handoff Summarization Service
─────────────────────────────────────────
Generates a concise (<150 word) third-person clinical briefing for a counselor
who is about to join an active crisis session. Fetches:
  - The user's most recent *resolved* escalation (historical context)
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
) -> str:
    """
    Returns a <150 word clinical handoff note for the incoming counselor.
    Falls back to a minimal template string if the LLM call fails, so the
    counselor always receives *something* rather than an empty brief.
    """
    db = get_database()
    if db is None:
        return _fallback_summary(crisis_category)

    try:
        past_context_str = await _fetch_past_session_context(db, user_id)
        current_context_str = await _fetch_current_session_context(db, current_session_id)

        system_prompt = (
            "You are a clinical handoff assistant for mental health professionals. "
            "Write a concise, third-person clinical briefing for a counselor who is about "
            "to join an active crisis session. Do not include any preamble or labels. "
            "Output only the briefing note. Keep it under 150 words."
        )

        user_prompt = (
            f"CRISIS CATEGORY: {crisis_category}\n\n"
            f"PRIOR SESSION HISTORY:\n{past_context_str}\n\n"
            f"CURRENT SESSION (AI conversation leading to crisis):\n{current_context_str}\n\n"
            "Write the clinical handoff note now."
        )

        settings = get_settings()
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=250,
            temperature=0.3,
        )

        summary = response.choices[0].message.content.strip()
        logger.info(f"[SUMMARIZATION] Handoff generated for session {current_session_id} ({len(summary)} chars)")
        return summary

    except Exception as e:
        logger.exception(f"[SUMMARIZATION] LLM call failed for session {current_session_id}: {e}")
        return _fallback_summary(crisis_category)


# ── Private helpers ───────────────────────────────────────────────────────────

async def _fetch_past_session_context(db, user_id: str) -> str:
    """Returns a formatted transcript from the user's last resolved escalation."""
    try:
        past_session = await db.sessions.find_one(
            {
                "user_id": user_id,
                "is_escalated": False,
                "assigned_counselor_id": {"$ne": None},
            },
            sort=[("created_at", -1)],
        )

        if not past_session:
            return "No prior escalation history found."

        cursor = db.messages.find(
            {"session_id": past_session["session_id"]},
            sort=[("timestamp", 1)],
        )
        messages = await cursor.to_list(length=30)
        return _format_messages(messages) or "No prior escalation history found."
    except Exception as e:
        logger.error(f"[SUMMARIZATION] Past context fetch failed for user {user_id}: {e}")
        return "No prior escalation history found."


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
