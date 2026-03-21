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
from app.services.safety import synthesize_consensus
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
    logger.info("Generating personalized opening message for new session.")
    session_id = str(uuid.uuid4())
    message = await llm_svc.get_opening_message(profile)
    logger.info(f"Opening message generated. Session ID: {session_id}")
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
        logger.warning("Empty message received")
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    logger.info(f"Received new message for session {req.session_id}")
    profile_dict = req.profile.model_dump()

    # Build context window for emotion model (last bot + last user turn)
    context_window = None
    if req.history and len(req.history) >= 2:
        recent = req.history[-4:]   # up to last 2 turns
        context_window = " ".join(m.get("content", "") for m in recent)

    # Run emotion analysis with conversational context for short messages
    try:
        emotion_result = await emotion_svc.analyse(req.message, context_window=context_window)
    except Exception as e:
        logger.error(f"Emotion analysis failed: {e}")
        emotion_result = None

    sadness_now = emotion_result.scores.get("sadness", 0.0) if emotion_result else 0.0
    updated_sadness = (req.sadness_scores + [sadness_now])[-10:]  # keep last 10

    logger.info("Chat — Running LLM Consensus Synthesizer...")
    try:
        consensus = await synthesize_consensus(
            text=req.message,
            roberta_emotion=emotion_result.dominant if emotion_result else "neutral",
            roberta_score=sadness_now
        )
    except Exception as e:
        logger.error(f"Chat — Consensus Synthesizer failed: {e}")
        consensus = {
            "llm_sentiment": "neutral", 
            "category": "general", 
            "is_crisis": False, 
            "reasoning": "fallback"
        }

    # Call LLM with full context
    reply = await llm_svc.chat(
        user_message=req.message,
        profile=profile_dict,
        history=req.history,
        consensus=consensus,
    )

    emotion_data = EmotionData(
        dominant_emotion=emotion_result.dominant if emotion_result else "neutral",
        top_scores=emotion_result.scores if emotion_result else {},
        response_mode=consensus.get("category", "general"),
        is_crisis_signal=consensus.get("is_crisis", False),
    )

    logger.info(f"Message processed successfully, returning reply. (Session: {req.session_id})")

    return ChatResponse(
        reply=reply,
        emotion=emotion_data,
        session_id=req.session_id,
        sadness_scores=updated_sadness,
    )


# ── SSE Streaming endpoint ─────────────────────────────────────────────────
@router.post("/stream")
async def stream_message(req: ChatRequest):
    """
    Server-Sent Events endpoint. Streams tokens as they arrive from Groq.
    Frontend connects with fetch + ReadableStream to render typing-in-progress.
    """
    profile_dict = req.profile.model_dump()

    logger.info("\n" + "═" * 70)
    logger.info(f"[NEW REQUEST] Session: {req.session_id}")
    logger.info(f"[USER QUERY]: {req.message}")
    logger.info("═" * 70)

    # Build context window for short-message emotion accuracy
    context_window = None
    if req.history and len(req.history) >= 2:
        recent = req.history[-4:]
        context_window = " ".join(m.get("content", "") for m in recent)

    try:
        logger.info("[STEP 1] Running Emotion Analysis (RoBERTa)...")
        emotion_result = await emotion_svc.analyse(req.message, context_window=context_window)
        if emotion_result:
            logger.info(f"[STEP 1 RESULT] Dominant: {emotion_result.dominant} | Top scores: {emotion_result.scores}")
    except Exception as e:
        logger.error(f"[STEP 1 ERROR] Emotion analysis failed: {e}")
        emotion_result = None

    sadness_now = emotion_result.scores.get("sadness", 0.0) if emotion_result else 0.0
    updated_sadness = (req.sadness_scores + [sadness_now])[-10:]

    logger.info("[STEP 2] Running Safety & Consensus Synthesizer (Llama-3-8B)...")
    try:
        consensus = await synthesize_consensus(
            text=req.message,
            roberta_emotion=emotion_result.dominant if emotion_result else "neutral",
            roberta_score=sadness_now
        )
        logger.info(f"[STEP 2 RESULT] Category: {consensus.get('category')} | Crisis: {consensus.get('is_crisis')} | Reason: {consensus.get('reasoning')}")
    except Exception as e:
        logger.error(f"[STEP 2 ERROR] Consensus Synthesizer failed: {e}")
        consensus = {
            "llm_sentiment": "neutral", 
            "category": "general", 
            "is_crisis": False, 
            "reasoning": "fallback"
        }

    logger.info("[STEP 3] Generating AI Response (Groq stream)...")

    async def generate():
        full_reply = []
        try:
            async for chunk in llm_svc.chat_stream(
                user_message=req.message,
                profile=profile_dict,
                history=req.history,
                consensus=consensus,
            ):
                full_reply.append(chunk)
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"

            emotion_dict = {
                "dominant_emotion": emotion_result.dominant if emotion_result else "neutral",
                "response_mode": consensus.get("category", "general"),
                "is_crisis_signal": consensus.get("is_crisis", False),
                "sadness_scores": updated_sadness,
            }
            yield f"data: {json.dumps({'done': True, 'emotion': emotion_dict})}\n\n"
            
            # Log the final assembled reply
            final_reply_text = ''.join(full_reply)
            logger.info("[STEP 3 RESULT] Streaming complete.")
            logger.info(f"[FINAL AI RESPONSE]:\n{final_reply_text}\n" + "═" * 70)

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