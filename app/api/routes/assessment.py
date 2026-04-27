"""
Assessment Route
────────────────
POST /api/assessment — One-time onboarding from Android.
Saves personality, creates session, returns opening message.
If the user already has a session, returns the existing session_id.
"""

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends

from app.api.schemas.request import AssessmentRequest
from app.api.schemas.response import AssessmentResponse
from app.services import llm as llm_svc
from app.core.logger import get_logger
from app.core.auth.oauth2 import get_current_user
from app.core.database import get_database
from app.services.db_service import build_personality_summary
from app.services.db_service import save_message

logger = get_logger(__name__)
router = APIRouter(prefix="/api/assessment", tags=["assessment"])


@router.post("", response_model=AssessmentResponse)
async def submit_assessment(req: AssessmentRequest, current_user = Depends(get_current_user)):
    """
    Called when a user completes onboarding in Android.
    1. Saves/updates personality to DB
    2. Checks if a session already exists for this user
       - If YES: returns the existing session_id (no new session created)
       - If NO:  creates a new session + generates opening message
    """

    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required. Please log in.")

    user_id = str(current_user.get("user_id") or current_user.get("_id"))
    logger.info(f"Assessment received for user: {user_id}")

    db = get_database()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    # 1. Save/update personality only
    personality_dict = req.personality_answers.model_dump()
    personality_summary = build_personality_summary(personality_dict)

    update_doc = {
        # "user_id": user_id,
        "personality_answers": personality_dict,
        "personality_summary": personality_summary,
        "last_active": datetime.now(timezone.utc),
    }

    from bson import ObjectId

    query = [{"user_id": user_id}]
    if ObjectId.is_valid(user_id):
        query.append({"_id": ObjectId(user_id)})

    update_doc.pop("user_id", None)
    update_doc.pop("_id", None)

    await db.users.update_one(
        # {"$or": query},
        {"user_id": user_id},
        {"$set": update_doc}
    )

    # 2. Check if this user already has a session
    existing = await db.sessions.find_one({"user_id": user_id}, sort=[("created_at", -1)])
    if existing:
        session_id = existing.get("session_id")
        logger.info(f"Reusing existing session {session_id} for user {user_id}")
        return AssessmentResponse(
            status="success",
            session_id=session_id,
            opening_message="Welcome back! How are you feeling today?",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
        )

    # 3. No session exists — create a new one
    session_id = str(uuid.uuid4())
    try:
        await db.sessions.insert_one({
            "session_id": session_id,
            "user_id": user_id,
            "is_active": True,
            "lethality_alert": False,
            "is_escalated": False,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        })
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        raise HTTPException(status_code=500, detail="Failed to create session")

    # 4. Generate opening message
    llm_profile = {
        "name": "Friend",  # Default name since personal info is filtered
        "personality_summary": personality_summary,
        "country": "IN",
    }

    opening = await llm_svc.get_opening_message(llm_profile)

    # 5. Save opening message to DB so it's in the history
    await save_message({
        "session_id": session_id,
        "user_id": user_id,
        "turn_number": 0,
        "role": "assistant",
        "content": opening,
    })

    logger.info(f"Assessment complete. New session: {session_id}")

    return AssessmentResponse(
        status="success",
        session_id=session_id,
        opening_message=opening,
        timestamp=datetime.now(timezone.utc),
        user_id=user_id,
    )