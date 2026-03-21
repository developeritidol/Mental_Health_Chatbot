"""
Chat Routes
────────────
POST /api/chat/opening   — personalised first message after intake
POST /api/chat/message   — standard request/response
POST /api/chat/stream    — SSE streaming (real-time typing effect)

v2 changes:
  • build_system_prompt() signature updated: user_message= replaces input_length=
    This is critical — the message classifier inside llm.py needs the actual
    message text to assign the correct token budget. An empty string causes
    every message to use the wrong (too large) budget.
  • recent_history string now passed to synthesize_consensus() so the 8B model
    has conversational context when classifying short or ambiguous messages.
  • SYNTHESIZER_MODEL pulled from settings (no hardcoded string in routes).
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


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_recent_history_string(history: list[dict], n_turns: int = 4) -> str:
    """
    Returns the last n_turns as a plain text string for the synthesizer.
    Gives the 8B model conversational context when the current message is short
    or ambiguous (e.g. "i did it" needs prior context to classify correctly).
    """
    if not history:
        return ""
    recent = history[-(n_turns * 2):]
    lines  = []
    for msg in recent:
        role    = "User" if msg.get("role") == "user" else "MindBridge"
        content = msg.get("content", "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


# ── Opening message ────────────────────────────────────────────────────────────

@router.post("/opening", response_model=OpeningMessageResponse)
async def get_opening_message(profile: dict):
    """
    Returns a personalised opening message based on the user's intake profile.
    Called once immediately after the intake questionnaire completes.
    """
    logger.info("Generating personalised opening message for new session.")
    session_id = str(uuid.uuid4())
    message    = await llm_svc.get_opening_message(profile)
    logger.info(f"Opening message generated. Session ID: {session_id}")
    return OpeningMessageResponse(message=message, session_id=session_id)


# ── Standard message exchange ──────────────────────────────────────────────────

@router.post("/message", response_model=ChatResponse)
async def send_message(req: ChatRequest):
    """
    Main chat endpoint (non-streaming).
    1. Runs emotion analysis (RoBERTa) on user message
    2. Runs consensus synthesizer (Llama-8B) with emotion + context
    3. Calls LLM generator with enriched system prompt
    4. Returns reply + emotion metadata
    """
    if not req.message.strip():
        logger.warning("Empty message received")
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    logger.info(f"New message — session {req.session_id}")
    profile_dict = req.profile.model_dump()

    # Build context string for emotion model and synthesizer
    recent_history_str = _build_recent_history_string(req.history, n_turns=4)

    # Context window for short-message emotion accuracy
    context_window = recent_history_str if recent_history_str else None

    # Step 1 — Emotion analysis
    try:
        emotion_result = await emotion_svc.analyse(req.message, context_window=context_window)
    except Exception as e:
        logger.error(f"Emotion analysis failed: {e}")
        emotion_result = None

    sadness_now    = emotion_result.scores.get("sadness", 0.0) if emotion_result else 0.0
    updated_sadness = (req.sadness_scores + [sadness_now])[-10:]

    # Step 2 — Consensus synthesis
    logger.info("Running LLM Consensus Synthesizer...")
    try:
        consensus = await synthesize_consensus(
            text=req.message,
            roberta_emotion=emotion_result.dominant if emotion_result else "neutral",
            roberta_score=sadness_now,
            recent_history=recent_history_str,          # v2: pass context
        )
    except Exception as e:
        logger.error(f"Consensus Synthesizer failed: {e}")
        consensus = {
            "llm_sentiment":    "neutral",
            "category":         "general",
            "intensity":        "moderate",
            "is_crisis":        False,
            "crisis_type":      None,
            "reasoning":        "fallback",
            "recommended_tone": "validating",
        }

    # Step 3 — LLM generation
    # CRITICAL: pass user_message= (not input_length=) so the message
    # classifier in build_system_prompt() gets the actual text
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

    logger.info(f"Message processed. Session: {req.session_id}")
    return ChatResponse(
        reply=reply,
        emotion=emotion_data,
        session_id=req.session_id,
        sadness_scores=updated_sadness,
    )


# ── SSE Streaming ──────────────────────────────────────────────────────────────

@router.post("/stream")
async def stream_message(req: ChatRequest):
    """
    Server-Sent Events endpoint. Streams tokens as they arrive from Groq.
    Frontend connects with fetch + ReadableStream for the typing-in-progress effect.
    """
    profile_dict = req.profile.model_dump()

    logger.info("\n" + "═" * 70)
    logger.info(f"[STREAM] Session: {req.session_id}")
    logger.info(f"[USER]:  {req.message}")
    logger.info("═" * 70)

    # Build context for synthesizer
    recent_history_str = _build_recent_history_string(req.history, n_turns=4)
    context_window     = recent_history_str if recent_history_str else None

    # Step 1 — Emotion analysis
    try:
        logger.info("[STEP 1] RoBERTa emotion analysis...")
        emotion_result = await emotion_svc.analyse(req.message, context_window=context_window)
        if emotion_result:
            logger.info(
                f"[STEP 1 OK] Dominant: {emotion_result.dominant} | "
                f"Top scores: {dict(list(emotion_result.scores.items())[:3])}"
            )
    except Exception as e:
        logger.error(f"[STEP 1 ERROR] Emotion analysis failed: {e}")
        emotion_result = None

    sadness_now     = emotion_result.scores.get("sadness", 0.0) if emotion_result else 0.0
    updated_sadness = (req.sadness_scores + [sadness_now])[-10:]

    # Step 2 — Consensus synthesis
    logger.info("[STEP 2] LLM Consensus Synthesizer (Llama-8B)...")
    try:
        consensus = await synthesize_consensus(
            text=req.message,
            roberta_emotion=emotion_result.dominant if emotion_result else "neutral",
            roberta_score=sadness_now,
            recent_history=recent_history_str,          # v2: pass context
        )
        logger.info(
            f"[STEP 2 OK] Category: {consensus.get('category')} | "
            f"Intensity: {consensus.get('intensity')} | "
            f"Tone: {consensus.get('recommended_tone')} | "
            f"Crisis: {consensus.get('is_crisis')}"
        )
    except Exception as e:
        logger.error(f"[STEP 2 ERROR] Synthesizer failed: {e}")
        consensus = {
            "llm_sentiment":    "neutral",
            "category":         "general",
            "intensity":        "moderate",
            "is_crisis":        False,
            "crisis_type":      None,
            "reasoning":        "fallback",
            "recommended_tone": "validating",
        }

    logger.info("[STEP 3] LLM Generator streaming (Groq)...")

    async def generate():
        full_reply = []
        try:
            # CRITICAL: user_message= (not input_length=) — message classifier needs this
            async for chunk in llm_svc.chat_stream(
                user_message=req.message,
                profile=profile_dict,
                history=req.history,
                consensus=consensus,
            ):
                full_reply.append(chunk)
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"

            # Done event — sends emotion metadata to frontend
            emotion_dict = {
                "dominant_emotion":  emotion_result.dominant if emotion_result else "neutral",
                "response_mode":     consensus.get("category", "general"),
                "intensity":         consensus.get("intensity", "moderate"),
                "recommended_tone":  consensus.get("recommended_tone", "validating"),
                "is_crisis_signal":  consensus.get("is_crisis", False),
                "sadness_scores":    updated_sadness,
            }
            yield f"data: {json.dumps({'done': True, 'emotion': emotion_dict})}\n\n"

            final_reply = "".join(full_reply)
            logger.info(f"[STEP 3 OK] Stream complete ({len(final_reply)} chars)")
            logger.info(f"[AI RESPONSE]:\n{final_reply}\n" + "═" * 70)

        except Exception as e:
            logger.error(f"[STREAM ERROR]: {e}")
            yield f"data: {json.dumps({'error': 'Stream interrupted'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
        },
    )