import logging
from typing import List, Dict, Optional
from datetime import datetime, timezone
from openai import AsyncOpenAI
from app.core.database import get_database
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Embeddings ────────────────────────────────────────────────────────────────

async def generate_embedding(text: str) -> List[float]:
    """
    Converts text into a 1536-dimensional vector using OpenAI's
    text-embedding-3-small model. This is the cheapest, fastest model
    and is more than sufficient for RAG chat retrieval.
    Returns empty list on failure (embedding is non-critical).
    """
    try:
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        response = await client.embeddings.create(
            input=text,
            model="text-embedding-3-small",
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Embedding generation failed: {e}")
        return []


async def retrieve_long_term_memory(
    device_id: str,
    query_vector: List[float],
    exclude_session_id: str = "",
    limit: int = 4,
) -> List[str]:
    """
    Searches ALL past messages belonging to this device_id using a
    cosine-similarity $vectorSearch on MongoDB Atlas.
    Returns plain-text snippets of the most relevant past turns.
    Returns empty list on failure or if no index exists yet.
    """
    if not query_vector:
        return []

    db = get_database()
    if db is None:
        return []

    try:
        pipeline = [
            {
                "$vectorSearch": {
                    "index": "messages_vector_index",
                    "path": "embedding",
                    "queryVector": query_vector,
                    "numCandidates": 50,
                    "limit": limit + 2,  # fetch extra to allow exclusion filter
                    "filter": {"device_id": device_id},
                }
            },
            # Exclude current session to avoid echo chamber
            {"$match": {"session_id": {"$ne": exclude_session_id}}},
            {"$limit": limit},
            {"$project": {"role": 1, "content": 1, "_id": 0}},
        ]
        cursor = db.messages.aggregate(pipeline)
        docs = await cursor.to_list(length=limit)

        snippets = []
        for doc in docs:
            role = "User" if doc.get("role") == "user" else "MindBridge"
            content = doc.get("content", "")[:200].strip()
            if content:
                snippets.append(f"{role}: {content}")

        if snippets:
            logger.info(f"Long-term memory: {len(snippets)} relevant past turns retrieved for {device_id}")
        return snippets

    except Exception as e:
        # Graceful fallback — if no vector index exists yet, just skip
        logger.warning(f"Vector search unavailable (index may not exist yet): {e}")
        return []


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
        "first_name": profile.get("first_name", ""),
        "last_name": profile.get("last_name"),
        "username": profile.get("username"),
        "gender": profile.get("gender"),
        "age": profile.get("age"),
        "emergency_contact": {
            "name": profile.get("emergency_contact_name"),
            "relation": profile.get("emergency_contact_relation"),
            "phone": profile.get("emergency_contact_phone"),
        },
        "personality_answers": personality_answers,
        "personality_summary": personality_summary,
        "last_active": datetime.now(timezone.utc),
    }

    try:
        await db.users.update_one(
            {"device_id": device_id},
            {"$set": update_doc, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
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
            "name": f"{doc.get('first_name', 'Friend')} {doc.get('last_name', '')}".strip() or "Friend",
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

async def get_existing_session(device_id: str) -> Optional[dict]:
    """
    Returns the existing session for a device_id, if one exists.
    Enforces the one-device-one-session rule.
    """
    db = get_database()
    if db is None:
        return None
    try:
        doc = await db.sessions.find_one(
            {"device_id": device_id},
            sort=[("created_at", -1)],  # get the most recent one
        )
        if doc:
            return {
                "session_id": doc.get("session_id"),
                "device_id": doc.get("device_id"),
                "is_active": doc.get("is_active", True),
                "is_escalated": doc.get("is_escalated", False),
            }
        return None
    except Exception as e:
        logger.error(f"Failed to lookup session for {device_id}: {e}")
        return None


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
            "is_escalated": False,          # True once handed off to a human
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        await db.sessions.insert_one(doc)
        return True
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        return False


async def escalate_session(session_id: str) -> bool:
    """
    Marks a session as escalated to a human operator.
    Called immediately when safety.py detects is_crisis == True.
    """
    db = get_database()
    if db is None:
        return False
    try:
        await db.sessions.update_one(
            {"session_id": session_id},
            {"$set": {
                "is_escalated": True,
                "escalated_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        logger.warning(f"[ESCALATION] Session {session_id} handed off to human operator.")
        return True
    except Exception as e:
        logger.error(f"Failed to escalate session {session_id}: {e}")
        return False


async def escalate_device(device_id: str) -> bool:
    """
    Marks all sessions for a device as escalated to a human operator.
    Called immediately when safety.py detects is_crisis == True.
    """
    db = get_database()
    if db is None:
        return False
    try:
        await db.sessions.update_many(
            {"device_id": device_id},
            {"$set": {
                "is_escalated": True,
                "escalated_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        logger.warning(f"[ESCALATION] Device {device_id} handed off to human operator.")
        return True
    except Exception as e:
        logger.error(f"Failed to escalate device {device_id}: {e}")
        return False


async def close_escalation(session_id: str) -> bool:
    """
    Marks a session as no longer escalated.
    Called by the human counselor when they click 'End Session'.
    After this, the user's next message will go back to the AI.
    """
    db = get_database()
    if db is None:
        return False
    try:
        await db.sessions.update_one(
            {"session_id": session_id},
            {"$set": {
                "is_escalated": False,
                "escalation_closed_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        logger.info(f"[ESCALATION CLOSED] Session {session_id} returned to AI mode.")
        return True
    except Exception as e:
        logger.error(f"Failed to close escalation for {session_id}: {e}")
        return False


async def close_escalation_by_device(device_id: str) -> bool:
    """
    Marks all sessions for a device as no longer escalated.
    """
    db = get_database()
    if db is None:
        return False
    try:
        await db.sessions.update_many(
            {"device_id": device_id, "is_escalated": True},
            {"$set": {
                "is_escalated": False,
                "escalation_closed_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        logger.info(f"[ESCALATION CLOSED] All sessions for device {device_id} returned to AI mode.")
        return True
    except Exception as e:
        logger.error(f"Failed to close escalation for device {device_id}: {e}")
        return False


async def is_session_escalated(session_id: str) -> bool:
    """
    Checks whether a session is currently under human control.
    Used by chat.py to block AI responses during active human intervention.
    """
    db = get_database()
    if db is None:
        return False
    try:
        doc = await db.sessions.find_one({"session_id": session_id})
        if doc and doc.get("is_escalated") is True:
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to check escalation status for {session_id}: {e}")
        return False


async def is_device_escalated(device_id: str) -> bool:
    """
    Checks whether ANY session for this device is currently under human control.
    Used by chat.py to block AI responses during active human intervention.
    """
    db = get_database()
    if db is None:
        return False
    try:
        doc = await db.sessions.find_one({"device_id": device_id, "is_escalated": True})
        if doc:
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to check escalation status for device {device_id}: {e}")
        return False


# ── Messages ──────────────────────────────────────────────────────────────────

async def save_message(message_data: dict) -> bool:
    """
    Saves a single message (user or assistant) to MongoDB.
    For user messages, also generates and stores an embedding vector
    for long-term memory retrieval (RAG).
    """
    db = get_database()
    if db is None:
        return False

    doc = {
        "session_id": message_data.get("session_id"),
        "device_id": message_data.get("device_id"),  # Required for vector search filtering
        "turn_number": message_data.get("turn_number", 0),
        "role": message_data.get("role"),
        "content": message_data.get("content"),
        "timestamp": datetime.now(timezone.utc),
    }

    if "roberta_analysis" in message_data:
        doc["roberta_analysis"] = message_data["roberta_analysis"]
    if "llm_consensus" in message_data:
        doc["llm_consensus"] = message_data["llm_consensus"]

    # Generate and store embedding for ALL messages (user + AI) for full RAG retrieval
    if message_data.get("content"):
        embedding = await generate_embedding(message_data["content"])
        if embedding:
            doc["embedding"] = embedding

    try:
        await db.messages.insert_one(doc)
        await db.sessions.update_one(
            {"session_id": message_data.get("session_id")},
            {"$set": {"updated_at": datetime.now(timezone.utc)}},
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


async def get_session_messages(session_id: str) -> List[Dict]:
    """
    Retrieves ALL messages for a given session_id.
    Returns them sorted chronologically (oldest to newest) to rebuild the UI.
    """
    db = get_database()
    if db is None:
        logger.warning(f"DB not connected, returning empty history for session {session_id}")
        return []

    try:
        cursor = db.messages.find({"session_id": session_id}).sort("timestamp", 1)
        docs = await cursor.to_list(length=None)

        formatted = []
        for doc in docs:
            if doc.get("content"):
                formatted.append({
                    "session_id": doc.get("session_id", "unknown"),
                    "role": doc.get("role", "unknown"),
                    "content": doc.get("content", ""),
                    "timestamp": doc.get("timestamp").replace(tzinfo=timezone.utc) if doc.get("timestamp") else None,
                })

        logger.info(f"Fetched {len(formatted)} messages for session {session_id}")
        return formatted
    except Exception as e:
        logger.error(f"Failed to fetch messages for session {session_id}: {e}")
        return []


async def get_device_messages(device_id: str) -> List[Dict]:
    """
    Retrieves ALL messages for a given device_id.
    Returns them sorted chronologically (oldest to newest) to rebuild the UI.
    """
    db = get_database()
    if db is None:
        logger.warning(f"DB not connected, returning empty history for device {device_id}")
        return []

    try:
        cursor = db.messages.find({"device_id": device_id}).sort("timestamp", 1)
        docs = await cursor.to_list(length=None)

        formatted = []
        for doc in docs:
            if doc.get("content"):
                formatted.append({
                    "device_id": doc.get("device_id", "unknown"),
                    "role": doc.get("role", "unknown"),
                    "content": doc.get("content", ""),
                    "timestamp": doc.get("timestamp").replace(tzinfo=timezone.utc) if doc.get("timestamp") else None,
                })

        logger.info(f"Fetched {len(formatted)} messages for device {device_id}")
        return formatted
    except Exception as e:
        logger.error(f"Failed to fetch messages for device {device_id}: {e}")
        return []


async def get_all_sessions(device_id: str) -> List[Dict]:
    """
    Retrieves ALL sessions for a given device_id.
    Returns them sorted by creation time (newest first).
    """
    db = get_database()
    if db is None:
        logger.warning(f"DB not connected, returning empty sessions for {device_id}")
        return []

    try:
        cursor = db.sessions.find({"device_id": device_id}).sort("created_at", -1)
        docs = await cursor.to_list(length=None)

        sessions = []
        for doc in docs:
            sessions.append({
                "session_id": doc.get("session_id"),
                "device_id": doc.get("device_id"),
                "is_active": doc.get("is_active", False),
                "is_escalated": doc.get("is_escalated", False),
                "created_at": doc.get("created_at").replace(tzinfo=timezone.utc) if doc.get("created_at") else None,
                "updated_at": doc.get("updated_at").replace(tzinfo=timezone.utc) if doc.get("updated_at") else None,
            })

        logger.info(f"Fetched {len(sessions)} sessions for device {device_id}")
        return sessions
    except Exception as e:
        logger.error(f"Failed to fetch sessions for {device_id}: {e}")
        return []


async def get_escalated_sessions() -> List[Dict]:
    """
    Retrieves ALL sessions that have been escalated (is_escalated == True).
    Used by the Human Admin Dashboard to see which users need help.
    Joins the users collection to fetch the user's name.
    """
    db = get_database()
    if db is None:
        return []

    try:
        pipeline = [
            {"$match": {"is_escalated": True}},
            {"$sort": {"escalated_at": -1}},
            {
                "$lookup": {
                    "from": "users",
                    "localField": "device_id",
                    "foreignField": "device_id",
                    "as": "user_info"
                }
            },
            {
                "$addFields": {
                    "first_name": {
                        "$cond": {
                            "if": {"$gt": [{"$size": "$user_info"}, 0]},
                            "then": {"$arrayElemAt": ["$user_info.first_name", 0]},
                            "else": "Unknown"
                        }
                    },
                    "last_name": {
                        "$cond": {
                            "if": {"$gt": [{"$size": "$user_info"}, 0]},
                            "then": {"$arrayElemAt": ["$user_info.last_name", 0]},
                            "else": None
                        }
                    },
                    "username": {
                        "$cond": {
                            "if": {"$gt": [{"$size": "$user_info"}, 0]},
                            "then": {"$arrayElemAt": ["$user_info.username", 0]},
                            "else": None
                        }
                    }
                }
            },
            {
                "$project": {
                    "user_info": 0
                }
            }
        ]
        
        cursor = db.sessions.aggregate(pipeline)
        docs = await cursor.to_list(length=None)

        sessions = []
        for doc in docs:
            sessions.append({
                "session_id": doc.get("session_id"),
                "device_id": doc.get("device_id"),
                "first_name": doc.get("first_name", "Unknown"),
                "last_name": doc.get("last_name"),
                "username": doc.get("username"),
                "is_escalated": doc.get("is_escalated", False),
                "escalated_at": doc.get("escalated_at").replace(tzinfo=timezone.utc).isoformat() if doc.get("escalated_at") else None,
                "created_at": doc.get("created_at").replace(tzinfo=timezone.utc).isoformat() if doc.get("created_at") else None,
            })

        logger.info(f"Fetched {len(sessions)} escalated sessions.")
        return sessions
    except Exception as e:
        logger.error(f"Failed to fetch escalated sessions: {e}")
        return []


async def get_expired_escalated_sessions(timeout_minutes: int) -> List[Dict[str, str]]:
    """
    Finds escalated sessions that haven't been updated in `timeout_minutes` minutes.
    Returns a list of dicts containing `session_id` and `device_id`.
    """
    db = get_database()
    if db is None:
        return []

    from datetime import timedelta
    expiration_time = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)

    try:
        cursor = db.sessions.find({
            "is_escalated": True,
            "updated_at": {"$lte": expiration_time}
        })
        docs = await cursor.to_list(length=None)
        return [{"session_id": doc["session_id"], "device_id": doc.get("device_id", doc["session_id"])} for doc in docs]
    except Exception as e:
        logger.error(f"Failed to fetch expired escalated sessions: {e}")
        return []
