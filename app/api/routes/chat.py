"""
Chat Routes
────────────
POST /api/chat/opening   — returns the personalised first message after intake
POST /api/chat/message   — standard request/response
GET  /api/chat/stream    — SSE streaming (real-time typing effect)
"""

import uuid
import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import json

from app.api.schemas.request import ChatRequest
from app.api.schemas.response import ChatResponse, OpeningMessageResponse, EmotionData
from app.services import emotion as emotion_svc
from app.services import llm as llm_svc
from app.services.safety import check_crisis_signals, check_emotion_trend
from app.core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


# ── Opening message (called once after intake is complete) ─────────────────
@router.post("/opening", response_model=OpeningMessageResponse)
async def get_opening_message(profile: dict):
    """
    Returns a personalised opening message based on the user's intake profile.
    Called immediately after the intake questionnaire is complete.
    """
    session_id = str(uuid.uuid4())
    message = llm_svc.get_opening_message(profile)
    return OpeningMessageResponse(message=message, session_id=session_id)


# ── Standard message exchange ──────────────────────────────────────────────
@router.post("/message", response_model=ChatResponse)
async def send_message(req: ChatRequest):
    """
    Main chat endpoint.
    1. Runs emotion analysis on the user's message (parallel is ideal in prod)
    2. Checks safety signals (backend only — doesn't affect conversation)
    3. Calls Groq LLM with enriched system prompt
    4. Returns reply + emotion metadata
    """
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    profile_dict = req.profile.model_dump()

    # Run emotion analysis
    try:
        emotion_result = await emotion_svc.analyse(req.message)
    except Exception as e:
        logger.error(f"Emotion analysis failed: {e}")
        emotion_result = None

    # Safety check (fire-and-forget for logging)
    try:
        safety = check_crisis_signals(req.message, req.session_id)
        if safety["is_crisis"]:
            logger.warning(f"[CRISIS SIGNAL] session={req.session_id}")
    except Exception as e:
        logger.warning(f"Safety check failed silently: {e}")

    # Emotion trend check (for monitoring)
    if emotion_result and req.sadness_scores:
        sadness_now = emotion_result.scores.get("sadness", 0.0)
        trend = check_emotion_trend(req.sadness_scores + [sadness_now])
        if trend["trending_down"] and trend["severity"] == "high":
            logger.warning(
                f"[TREND] Escalating sadness in session {req.session_id}: {trend}"
            )

    # Call LLM
    reply = await llm_svc.chat(
        user_message=req.message,
        profile=profile_dict,
        history=req.history,
        emotion=emotion_result,
    )

    emotion_data = EmotionData(
        dominant_emotion=emotion_result.dominant if emotion_result else "neutral",
        top_scores=emotion_result.scores if emotion_result else {},
        response_mode=emotion_result.mode if emotion_result else "curious_exploration",
        is_crisis_signal=emotion_result.is_crisis_signal if emotion_result else False,
    )

    return ChatResponse(
        reply=reply,
        emotion=emotion_data,
        session_id=req.session_id,
    )


# ── SSE Streaming endpoint ─────────────────────────────────────────────────
@router.post("/stream")
async def stream_message(req: ChatRequest):
    """
    Server-Sent Events endpoint. Streams tokens as they arrive from Groq.
    Frontend connects with fetch + ReadableStream to render typing-in-progress.
    """
    profile_dict = req.profile.model_dump()

    try:
        emotion_result = await emotion_svc.analyse(req.message)
    except Exception:
        emotion_result = None

    async def generate():
        full_reply = []
        try:
            async for chunk in llm_svc.chat_stream(
                user_message=req.message,
                profile=profile_dict,
                history=req.history,
                emotion=emotion_result,
            ):
                full_reply.append(chunk)
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"

            # Send final metadata event
            emotion_dict = {
                "dominant_emotion": emotion_result.dominant if emotion_result else "neutral",
                "response_mode": emotion_result.mode if emotion_result else "curious_exploration",
                "is_crisis_signal": emotion_result.is_crisis_signal if emotion_result else False,
            }
            yield f"data: {json.dumps({'done': True, 'emotion': emotion_dict})}\n\n"

        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield f"data: {json.dumps({'error': 'Stream interrupted'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )