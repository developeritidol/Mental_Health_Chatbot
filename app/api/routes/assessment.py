"""
Assessment Route
────────────────
POST /api/assessment — One-time onboarding from Android.
Saves profile + personality, creates session, returns opening message.
"""

import uuid
from fastapi import APIRouter, HTTPException

from app.api.schemas.request import AssessmentRequest
from app.api.schemas.response import AssessmentResponse
from app.services.db_service import upsert_user_profile, create_session
from app.services import llm as llm_svc
from app.core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/assessment", tags=["assessment"])


@router.post("", response_model=AssessmentResponse)
async def submit_assessment(req: AssessmentRequest):
    """
    Called once when a new user completes onboarding in Android.
    1. Saves profile + personality to DB
    2. Creates a new chat session
    3. Generates a warm opening message
    4. Returns session_id + opening_message
    """
    logger.info(f"Assessment received for device: {req.device_id}")

    # 1. Save profile + personality
    profile_dict = req.profile.model_dump()
    personality_dict = req.personality_answers.model_dump()

    saved = await upsert_user_profile(
        device_id=req.device_id,
        profile=profile_dict,
        personality_answers=personality_dict,
    )
    if not saved:
        raise HTTPException(status_code=500, detail="Failed to save profile")

    # 2. Create session
    session_id = str(uuid.uuid4())
    await create_session({
        "session_id": session_id,
        "device_id": req.device_id,
    })

    # 3. Generate opening message
    # Build the profile dict that llm_svc.get_opening_message expects
    from app.services.db_service import build_personality_summary
    llm_profile = {
        "name": profile_dict.get("name", "Friend"),
        "gender": profile_dict.get("gender", ""),
        "age": profile_dict.get("age"),
        "personality_summary": build_personality_summary(personality_dict),
        "country": "IN",
    }

    opening = await llm_svc.get_opening_message(llm_profile)

    # 4. Save opening message to DB so it's in the history
    from app.services.db_service import save_message
    await save_message({
        "session_id": session_id,
        "turn_number": 0,
        "role": "assistant",
        "content": opening,
    })

    logger.info(f"Assessment complete. Session: {session_id}")

    return AssessmentResponse(
        status="success",
        session_id=session_id,
        opening_message=opening,
        device_id=req.device_id,
    )