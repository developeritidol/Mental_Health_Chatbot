"""
Chat Route
──────────
POST /api/chat/stream — SSE streaming chat.
Android sends only: session_id, device_id, message.
Server loads profile and full history from MongoDB.
"""

import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api.schemas.request import StreamChatRequest
from app.services import emotion as emotion_svc
from app.services import llm as llm_svc
from app.services.safety import synthesize_consensus
from app.services.db_service import (
    get_user_profile,
    get_formatted_history,
    save_message,
    generate_embedding,
    retrieve_long_term_memory,
)
from app.core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_recent_history_string(history: list[dict], n_turns: int = 4) -> str:
    if not history:
        return ""
    recent = history[-(n_turns * 2):]
    lines = []
    for msg in recent:
        role = "User" if msg.get("role") == "user" else "MindBridge"
        content = msg.get("content", "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _safe_fallback_consensus() -> dict:
    return {
        "llm_sentiment":    "neutral",
        "category":         "general",
        "intensity":        "moderate",
        "is_crisis":        False,
        "crisis_type":      None,
        "reasoning":        "fallback",
        "recommended_tone": "validating",
        "message_class":    "emotional_ongoing",
        "token_budget":     320,
    }


# ── SSE Stream ─────────────────────────────────────────────────────────────────

@router.post("/stream")
async def stream_message(req: StreamChatRequest):
    """
    Main chat endpoint for Android.
    Android sends: session_id + device_id + message.
    Server loads profile and history from MongoDB automatically.
    """
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    # 1. Load profile from DB
    profile = await get_user_profile(req.device_id)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Profile not found. Complete assessment first.",
        )

    # 2. Load full conversation history from DB
    history = await get_formatted_history(req.session_id, limit=100)
    turn_count = len(history) // 2
    recent_history_str = _build_recent_history_string(history, n_turns=4)

    logger.info("\n" + "═" * 70)
    logger.info(f"[STREAM] Session: {req.session_id} | Turn: {turn_count}")
    logger.info(f"[USER]:  {req.message}")
    logger.info("═" * 70)

    # 3. Generate embedding and retrieve long-term memory (RAG)
    query_vector = await generate_embedding(req.message)
    long_term_memory = await retrieve_long_term_memory(
        device_id=req.device_id,
        query_vector=query_vector,
        exclude_session_id=req.session_id,
    )

    # 4. Save user message to DB (embedding is stored inside save_message automatically)
    await save_message({
        "session_id": req.session_id,
        "device_id": req.device_id,
        "turn_number": turn_count + 1,
        "role": "user",
        "content": req.message,
    })

    # 4. RoBERTa emotion analysis
    emotion_result = None
    try:
        logger.info("[STEP 1] RoBERTa emotion analysis...")
        emotion_result = await emotion_svc.analyse(
            req.message,
            context_window=recent_history_str or None,
        )
        if emotion_result:
            logger.info(
                f"[STEP 1 OK] {emotion_result.dominant} | "
                f"top: {dict(list(emotion_result.scores.items())[:3])}"
            )
    except Exception as e:
        logger.error(f"[STEP 1 ERROR] {e}")

    sadness_now = emotion_result.scores.get("sadness", 0.0) if emotion_result else 0.0

    # 5. Consensus synthesis
    logger.info("[STEP 2] LLM Consensus Synthesizer...")
    try:
        consensus = await synthesize_consensus(
            text=req.message,
            roberta_emotion=emotion_result.dominant if emotion_result else "neutral",
            roberta_score=sadness_now,
        )
        logger.info(
            f"[STEP 2 OK] crisis: {consensus.get('is_crisis')} | "
            f"category: {consensus.get('category')}"
        )
    except Exception as e:
        logger.error(f"[STEP 2 ERROR] {e}")
        consensus = _safe_fallback_consensus()

    # 6. Stream response with long-term memory injected
    async def generate():
        full_reply = []
        try:
            async for chunk in llm_svc.chat_stream(
                user_message=req.message,
                profile=profile,
                history=history,
                consensus=consensus,
                long_term_memory=long_term_memory,
            ):
                full_reply.append(chunk)
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"

            # Final SSE event with emotion metadata
            emotion_dict = {
                "dominant_emotion":  emotion_result.dominant if emotion_result else "neutral",
                "response_mode":     consensus.get("category", "general"),
                "intensity":         consensus.get("intensity", "moderate"),
                "is_crisis_signal":  consensus.get("is_crisis", False),
            }
            yield f"data: {json.dumps({'done': True, 'emotion': emotion_dict})}\n\n"

            # Save AI response to DB
            final = "".join(full_reply)
            logger.info(f"[STEP 3 OK] {len(final)} chars streamed")

            roberta_doc = None
            if emotion_result:
                roberta_doc = {
                    "dominant_emotion": emotion_result.dominant,
                    "scores": emotion_result.scores,
                }

            await save_message({
                "session_id": req.session_id,
                "device_id": req.device_id,
                "turn_number": turn_count + 1,
                "role": "assistant",
                "content": final,
                "roberta_analysis": roberta_doc,
                "llm_consensus": consensus,
            })

            logger.info(f"[AI RESPONSE]:\n{final}\n" + "═" * 70)

        except Exception as e:
            logger.error(f"[STREAM ERROR] {e}")
            yield f"data: {json.dumps({'error': 'Stream interrupted'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )