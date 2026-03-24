import logging
from typing import List, Dict, Optional
from datetime import datetime
from app.core.database import get_database

logger = logging.getLogger(__name__)


# ── Personality conversion ────────────────────────────────────────────────────

_PERSONALITY_MAP = {
    "prefers_solitude":    {"Yes": "Introverted",         "No": "Extroverted",        "Sometimes": "Ambivert"},
    "logic_over_emotion":  {"Yes": "Logic-driven",        "No": "Emotion-driven",     "Sometimes": "Balanced thinker"},
    "plans_ahead":         {"Yes": "Structured planner",  "No": "Spontaneous",        "Sometimes": "Flexible planner"},
    "energized_by_social": {"Yes": "Socially energized",  "No": "Socially drained",   "Sometimes": "Selectively social"},
    "trusts_instincts":    {"Yes": "Trusts gut feelings", "No": "Analytical decider", "Sometimes": "Situational decider"},
}


def build_personality_summary(answers: dict) -> str:
    """Converts raw personality answers into a human-readable summary for the LLM."""
    traits = []
    for key, options in _PERSONALITY_MAP.items():
        val = answers.get(key, "Sometimes")
        traits.append(options.get(val, options["Sometimes"]))
    return ", ".join(traits)


# ── User profile ──────────────────────────────────────────────────────────────

async def upsert_user_profile(device_id: str, profile: dict, personality_answers: dict) -> bool:
    """Saves or updates a user profile with personality data."""
    db = get_database()
    if db is None:
        logger.error("DB not connected")
        return False

    personality_summary = build_personality_summary(personality_answers)

    update_doc = {
        "name": profile.get("name", ""),
        "gender": profile.get("gender"),
        "age": profile.get("age"),
        "emergency_contact": {
            "name": profile.get("emergency_contact_name"),
            "relation": profile.get("emergency_contact_relation"),
            "phone": profile.get("emergency_contact_phone"),
        },
        "personality_answers": personality_answers,
        "personality_summary": personality_summary,
        "last_active": datetime.utcnow(),
    }

    try:
        await db.users.update_one(
            {"device_id": device_id},
            {"$set": update_doc, "$setOnInsert": {"created_at": datetime.utcnow()}},
            upsert=True,
        )
        logger.info(f"Profile saved for device {device_id}: {personality_summary}")
        return True
    except Exception as e:
        logger.error(f"Failed to upsert user profile: {e}")
        return False


async def get_user_profile(device_id: str) -> Optional[dict]:
    """Loads a user profile from DB. Returns None if not found."""
    db = get_database()
    if db is None:
        logger.warning("DB not connected")
        return None

    try:
        doc = await db.users.find_one({"device_id": device_id})
        if not doc:
            logger.warning(f"No profile found for device {device_id}")
            return None

        # Build the profile dict that the LLM service expects
        ec = doc.get("emergency_contact", {}) or {}
        return {
            "device_id": device_id,
            "name": doc.get("name", "Friend"),
            "gender": doc.get("gender", ""),
            "age": doc.get("age"),
            "personality_summary": doc.get("personality_summary", "Not provided"),
            "existing_conditions": "None",
            "country": "IN",
            "crisis_follow_up": False,
        }
    except Exception as e:
        logger.error(f"Failed to get profile for {device_id}: {e}")
        return None


# ── Session ───────────────────────────────────────────────────────────────────

async def create_session(session_data: dict) -> bool:
    """Creates a new session document."""
    db = get_database()
    if db is None:
        return False

    try:
        doc = {
            "session_id": session_data["session_id"],
            "device_id": session_data["device_id"],
            "is_active": True,
            "lethality_alert": False,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        await db.sessions.insert_one(doc)
        return True
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        return False


# ── Messages ──────────────────────────────────────────────────────────────────

async def save_message(message_data: dict) -> bool:
    """Saves a single message (user or assistant) to MongoDB."""
    db = get_database()
    if db is None:
        return False

    doc = {
        "session_id": message_data.get("session_id"),
        "turn_number": message_data.get("turn_number", 0),
        "role": message_data.get("role"),
        "content": message_data.get("content"),
        "timestamp": datetime.utcnow(),
    }

    if "roberta_analysis" in message_data:
        doc["roberta_analysis"] = message_data["roberta_analysis"]
    if "llm_consensus" in message_data:
        doc["llm_consensus"] = message_data["llm_consensus"]

    try:
        await db.messages.insert_one(doc)
        await db.sessions.update_one(
            {"session_id": message_data.get("session_id")},
            {"$set": {"updated_at": datetime.utcnow()}},
        )
        return True
    except Exception as e:
        logger.error(f"Failed to save message: {e}")
        return False


async def get_formatted_history(session_id: str, limit: int = 100) -> List[Dict[str, str]]:
    """
    Loads conversation history from MongoDB.
    Returns list of {role, content} dicts sorted chronologically.
    Limit set to 100 to give GPT-4o full conversation context.
    """
    db = get_database()
    if db is None:
        logger.warning(f"DB not connected, returning empty history for {session_id}")
        return []

    try:
        cursor = db.messages.find({"session_id": session_id}).sort("timestamp", -1).limit(limit)
        docs = await cursor.to_list(length=limit)
        docs.reverse()  # chronological order for LLM

        return [{"role": doc["role"], "content": doc["content"]} for doc in docs]
    except Exception as e:
        logger.error(f"Failed to fetch history for {session_id}: {e}")
        return []
