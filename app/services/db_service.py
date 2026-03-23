import logging
from typing import List, Dict, Optional
from datetime import datetime
from app.core.database import get_database

logger = logging.getLogger(__name__)

async def upsert_user_profile(profile_data: dict) -> bool:
    """Inserts or updates a user profile based on device_id."""
    db = get_database()
    if db is None:
        logger.error("DB not connected")
        return False
        
    device_id = profile_data.get("device_id", "unknown_device")
    
    # Map the schema payload to the UserModelDB format
    update_doc = {
        "name": profile_data.get("name", ""),
        "gender": profile_data.get("gender"),
        "age": profile_data.get("age"),
        "profession": profile_data.get("profession"),
        "existing_conditions": profile_data.get("existing_conditions"),
        "emergency_contact": {
            "name": profile_data.get("emergency_contact_name"),
            "relation": profile_data.get("emergency_contact_relation"),
            "phone": profile_data.get("emergency_contact_phone"),
        },
        "personality_summary": profile_data.get("personality_summary"),
        "last_active": datetime.utcnow()
    }
    
    try:
        await db.users.update_one(
            {"device_id": device_id},
            {"$set": update_doc, "$setOnInsert": {"created_at": datetime.utcnow()}},
            upsert=True
        )
        return True
    except Exception as e:
        logger.error(f"Failed to upsert user profile: {e}")
        return False

async def create_session(session_data: dict) -> bool:
    """Creates a new Session document."""
    db = get_database()
    if db is None: return False
    
    try:
        doc = {
            "session_id": session_data.get("session_id"),
            "device_id": session_data.get("device_id", "unknown_device"),
            "topic": session_data.get("topic", ""),
            "initial_mood_score": session_data.get("initial_mood_score", 0),
            "country": session_data.get("country", "IN"),
            "is_active": True,
            "lethality_alert": False,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        await db.sessions.insert_one(doc)
        return True
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        return False

async def save_message(message_data: dict) -> bool:
    """Saves a single message (user or assistant) to MongoDB."""
    db = get_database()
    if db is None: return False
    
    doc = {
        "session_id": message_data.get("session_id"),
        "turn_number": message_data.get("turn_number", 0),
        "role": message_data.get("role"),
        "content": message_data.get("content"),
        "timestamp": datetime.utcnow()
    }
    
    if "roberta_analysis" in message_data:
        doc["roberta_analysis"] = message_data["roberta_analysis"]
    if "llm_consensus" in message_data:
        doc["llm_consensus"] = message_data["llm_consensus"]
    if "tokens_used" in message_data:
        doc["tokens_used"] = message_data["tokens_used"]
        
    try:
        await db.messages.insert_one(doc)
        
        # Also update session last active time
        await db.sessions.update_one(
            {"session_id": message_data.get("session_id")},
            {"$set": {"updated_at": datetime.utcnow()}}
        )
        return True
    except Exception as e:
        logger.error(f"Failed to save message: {e}")
        return False

async def get_formatted_history(session_id: str, limit: int = 20) -> List[Dict[str, str]]:
    """
    Fetches the history from MongoDB, sorts chronologically, and 
    formats it strictly for the LLM injection `[{"role": str, "content": str}]`.
    """
    db = get_database()
    if db is None:
        logger.warning(f"DB not connected, returning empty history for {session_id}")
        return []
        
    try:
        cursor = db.messages.find({"session_id": session_id}).sort("timestamp", -1).limit(limit)
        docs = await cursor.to_list(length=limit)
        
        # Sort ascending for LLM context flow
        docs.reverse()
        
        history = []
        for doc in docs:
            history.append({
                "role": doc["role"],
                "content": doc["content"]
            })
        return history
    except Exception as e:
        logger.error(f"Failed to fetch history for {session_id}: {e}")
        return []
