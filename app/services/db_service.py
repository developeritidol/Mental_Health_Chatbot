import logging
from typing import List, Dict, Optional
from datetime import datetime
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
        "timestamp": datetime.utcnow(),
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


async def get_all_user_messages(device_id: str) -> List[Dict]:
    """
    Retrieves ALL past messages for a given device_id across all sessions.
    Returns them sorted chronologically (oldest to newest) to rebuild the UI.
    """
    db = get_database()
    if db is None:
        logger.warning(f"DB not connected, returning empty history for {device_id}")
        return []

    try:
        # Sort by timestamp ascending (1) to show oldest first in UI
        cursor = db.messages.find({"device_id": device_id}).sort("timestamp", 1)
        docs = await cursor.to_list(length=None)  # fetch all
        
        # Format for the response schema
        formatted = []
        for doc in docs:
            # Only return messages that have content
            if doc.get("content"):
                formatted.append({
                    "session_id": doc.get("session_id", "unknown"),
                    "role": doc.get("role", "unknown"),
                    "content": doc.get("content", ""),
                    "timestamp": doc.get("timestamp"),
                })
        
        logger.info(f"Fetched {len(formatted)} total historical messages for {device_id}")
        return formatted
    except Exception as e:
        logger.error(f"Failed to fetch all messages for {device_id}: {e}")
        return []
