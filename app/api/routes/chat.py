"""
Chat Routes v3
───────────────
POST /api/chat/opening   — personalised first message after intake
POST /api/chat/message   — standard request/response
POST /api/chat/stream    — SSE streaming (real-time typing effect)

v3 changes:
  • turn_count now passed to synthesize_consensus() so the 8B can correctly
    classify "first_disclosure" vs "emotional_ongoing" based on actual turn number.
  • Fallback consensus dict updated to include all v3 fields
    (message_class, token_budget) so llm.py never gets KeyError on fallback.
"""

import uuid
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
    Returns the last n_turns as plain text for the 8B synthesizer.
    Critical for short/ambiguous messages — "i did it" needs prior context
    to be classified correctly.
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


def _get_turn_count(history: list[dict]) -> int:
    """Returns number of completed conversation turns (user + assistant pairs)."""
    return len(history) // 2


def _safe_fallback_consensus() -> dict:
    """
    Complete fallback with all v3 fields.
    Used when synthesizer call fails — never produces a KeyError in llm.py.
    """
    return {
        "llm_sentiment":    "neutral",
        "category":         "general",
        "intensity":        "moderate",
        "is_crisis":        False,
        "crisis_type":      None,
        "reasoning":        "fallback",
        "recommended_tone": "validating",
        "message_class":    "emotional_ongoing",
        "token_budget":     200,
    }


# ── Opening message ────────────────────────────────────────────────────────────

@router.post("/opening", response_model=OpeningMessageResponse)
async def get_opening_message(profile: dict):
    """
    Returns a personalised opening message based on the user's intake profile.
    Called once after intake questionnaire completes.
    """
    logger.info("Generating opening message for new session.")
    session_id = str(uuid.uuid4())
    message    = await llm_svc.get_opening_message(profile)
    logger.info(f"Opening message generated. Session ID: {session_id}")
    return OpeningMessageResponse(message=message, session_id=session_id)


# ── Standard message exchange ──────────────────────────────────────────────────

@router.post("/message", response_model=ChatResponse)
async def send_message(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    logger.info(f"New message — session {req.session_id}")
    profile_dict       = req.profile.model_dump()
    turn_count         = _get_turn_count(req.history)
    recent_history_str = _build_recent_history_string(req.history, n_turns=4)

    # Step 1 — RoBERTa emotion
    try:
        emotion_result = await emotion_svc.analyse(
            req.message,
            context_window=recent_history_str or None,
        )
    except Exception as e:
        logger.error(f"Emotion analysis failed: {e}")
        emotion_result = None

    sadness_now     = emotion_result.scores.get("sadness", 0.0) if emotion_result else 0.0
    updated_sadness = (req.sadness_scores + [sadness_now])[-10:]

    # Step 2 — Consensus synthesis (now includes message_class + token_budget)
    try:
        consensus = await synthesize_consensus(
            text=req.message,
            roberta_emotion=emotion_result.dominant if emotion_result else "neutral",
            roberta_score=sadness_now,
            recent_history=recent_history_str,
            turn_count=turn_count,              # v3: needed for first_disclosure classification
        )
    except Exception as e:
        logger.error(f"Consensus Synthesizer failed: {e}")
        consensus = _safe_fallback_consensus()

    # Step 3 — LLM generation
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

    return ChatResponse(
        reply=reply,
        emotion=emotion_data,
        session_id=req.session_id,
        sadness_scores=updated_sadness,
    )


# ── SSE Streaming ──────────────────────────────────────────────────────────────

@router.post("/stream")
async def stream_message(req: ChatRequest):
    profile_dict       = req.profile.model_dump()
    turn_count         = _get_turn_count(req.history)
    recent_history_str = _build_recent_history_string(req.history, n_turns=4)

    logger.info("\n" + "═" * 70)
    logger.info(f"[STREAM] Session: {req.session_id} | Turn: {turn_count}")
    logger.info(f"[USER]:  {req.message}")
    logger.info("═" * 70)

    # Step 1 — RoBERTa
    try:
        logger.info("[STEP 1] RoBERTa emotion analysis...")
        emotion_result = await emotion_svc.analyse(
            req.message,
            context_window=recent_history_str or None,
        )
        if emotion_result:
            logger.info(f"[STEP 1 OK] {emotion_result.dominant} | top: {dict(list(emotion_result.scores.items())[:3])}")
    except Exception as e:
        logger.error(f"[STEP 1 ERROR] {e}")
        emotion_result = None

    sadness_now     = emotion_result.scores.get("sadness", 0.0) if emotion_result else 0.0
    updated_sadness = (req.sadness_scores + [sadness_now])[-10:]

    # Step 2 — Consensus (includes message_class + token_budget)
    logger.info("[STEP 2] LLM Consensus Synthesizer...")
    try:
        consensus = await synthesize_consensus(
            text=req.message,
            roberta_emotion=emotion_result.dominant if emotion_result else "neutral",
            roberta_score=sadness_now,
            recent_history=recent_history_str,
            turn_count=turn_count,              # v3: needed for first_disclosure
        )
        logger.info(
            f"[STEP 2 OK] class: {consensus.get('message_class')} | "
            f"tokens: {consensus.get('token_budget')} | "
            f"category: {consensus.get('category')} | "
            f"intensity: {consensus.get('intensity')} | "
            f"crisis: {consensus.get('is_crisis')}"
        )
    except Exception as e:
        logger.error(f"[STEP 2 ERROR] {e}")
        consensus = _safe_fallback_consensus()

    logger.info(f"[STEP 3] LLM Generator — class: {consensus.get('message_class')} | budget: {consensus.get('token_budget')} tokens")

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
                "dominant_emotion":  emotion_result.dominant if emotion_result else "neutral",
                "response_mode":     consensus.get("category", "general"),
                "message_class":     consensus.get("message_class", "emotional_ongoing"),
                "intensity":         consensus.get("intensity", "moderate"),
                "recommended_tone":  consensus.get("recommended_tone", "validating"),
                "is_crisis_signal":  consensus.get("is_crisis", False),
                "sadness_scores":    updated_sadness,
            }
            yield f"data: {json.dumps({'done': True, 'emotion': emotion_dict})}\n\n"

            final = "".join(full_reply)
            logger.info(f"[STEP 3 OK] {len(final)} chars")
            logger.info(f"[AI RESPONSE]:\n{final}\n" + "═" * 70)

        except Exception as e:
            logger.error(f"[STREAM ERROR] {e}")
            yield f"data: {json.dumps({'error': 'Stream interrupted'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )