import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

# Schemas
from app.api.schemas.request import StreamChatRequest
from app.api.schemas.response import (
    ChatHistoryResponse,
    ChatMessageResponse,
    SessionListResponse,
    SessionResponse,
)

# Core
from app.core.database import get_database
from app.core.auth.oauth2 import get_current_user
from app.core.logger import get_logger

# Services
from app.services import emotion as emotion_svc
from app.services import llm as llm_svc
from app.services.safety import synthesize_consensus
from app.services.db_service import (
    get_user_profile,
    get_formatted_history,
    save_message,
    generate_embedding,
    retrieve_long_term_memory,
    get_user_messages,
    get_all_sessions,
    escalate_session,
    is_user_escalated,
    get_existing_session,
)

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
async def stream_message(req: StreamChatRequest, current_user = Depends(get_current_user)):
    """
    Main chat endpoint for Android.
    Android sends: session_id + user_id + message.
    Server loads profile and history from MongoDB automatically.
    """
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    user_id = str(current_user.get("user_id") or current_user.get("_id"))

    # 1. Load profile from DB
    profile = await get_user_profile(user_id)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Profile not found. Complete assessment first.",
        )

    # Resolve active session if needed for backwards compat
    session_info = await get_existing_session(req.user_id)
    actual_session_id = session_info["session_id"] if session_info else req.user_id

    # 1b. Guard: If this session is currently escalated to a human,
    #     block AI and redirect Android back to the WebSocket.
    if await is_user_escalated(req.user_id):
        _settings = get_settings()
        ws_url = f"ws://{_settings.SERVER_HOST}:{_settings.SERVER_PORT}/api/human/chat/{req.user_id}"

        logger.info(f"[GUARD] User {req.user_id} is escalated. Blocking AI and sending redirect.")
        
        redirect_payload = {
            "done": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "escalation_active",
            "handoff_message": "You are currently connected to a human counselor. Please continue in the live chat.",
            "websocket_url": ws_url,
        }

        async def _redirect_stream():
            yield f"data: {json.dumps(redirect_payload)}\n\n"

        return StreamingResponse(
            _redirect_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 2. Load full conversation history from DB
    history = await get_formatted_history(actual_session_id, limit=100)
    turn_count = len(history) // 2
    recent_history_str = _build_recent_history_string(history, n_turns=4)

    logger.info("\n" + "═" * 70)
    logger.info(f"[STREAM] User: {req.user_id} | Session: {actual_session_id} | Turn: {turn_count}")
    logger.info(f"[USER]:  {req.message}")
    logger.info("═" * 70)

    # 3. Generate embedding and retrieve long-term memory (RAG)
    query_vector = await generate_embedding(req.message)
    long_term_memory = await retrieve_long_term_memory(
        user_id=req.user_id,
        query_vector=query_vector,
        exclude_session_id=actual_session_id,
    )

    # 4. Save user message to DB (embedding is stored inside save_message automatically)
    await save_message({
        "session_id": actual_session_id,
        "user_id": req.user_id,
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

    # ── STEP 6: Crisis fork — escalate to human but stream normally ───────────
    if consensus.get("is_crisis") is True:
        logger.warning(f"[ESCALATION] Crisis detected for user {req.user_id}. Escalating in background and streaming AI response consistently.")
        await escalate_session(actual_session_id)
        
        
        await manager.broadcast_to_dashboard({
            "type": "new_escalation",
            "session_id": actual_session_id,
            "user_id": req.user_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    # ── STEP 7: Normal AI stream ───────────────────────────────────────────────
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

            # Final SSE event with emotion metadata and timestamp
            emotion_dict = {
                "dominant_emotion":  emotion_result.dominant if emotion_result else "neutral",
                "response_mode":     consensus.get("category", "general"),
                "intensity":         consensus.get("intensity", "moderate"),
                "is_crisis_signal":  consensus.get("is_crisis", False),
            }
            done_payload = {
                "done": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "emotion": emotion_dict
            }
            if consensus.get("is_crisis") is True:
                done_payload["handoff_message"] = "A counselor is joining shortly... you're not alone."
            
            yield f"data: {json.dumps(done_payload)}\n\n"

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
                "session_id": actual_session_id,
                "user_id": req.user_id,
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


# ── Chat History API (by session_id) ──────────────────────────────────────────

@router.get("/history", response_model=ChatHistoryResponse)
async def get_chat_history(current_user = Depends(get_current_user)):
    """
    Returns ALL messages for the authenticated user, sorted chronologically.
    Used by Android to load conversation history when opening a session.
    """
    db = get_database()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed.")

    user_id = str(current_user.get("user_id") or current_user.get("_id"))

    cursor = db.messages.find({"user_id": user_id}).sort("timestamp", 1)
    docs = await cursor.to_list(length=None)

    formatted_messages = []
    for doc in docs:
        if doc.get("content"):
            formatted_messages.append(
                ChatMessageResponse(
                    user_id=doc.get("user_id", user_id),
                    role=doc.get("role", "unknown"),
                    content=doc.get("content", ""),
                    timestamp=doc.get("timestamp").replace(tzinfo=timezone.utc) if doc.get("timestamp") else None
                )
            )

    return ChatHistoryResponse(
        status="success",
        user_id=user_id,
        total_messages=len(formatted_messages),
        messages=formatted_messages,
    )


# ── Sessions List API (by user_id) ─────────────────────────────────────────

@router.get("/sessions", response_model=SessionListResponse)
async def get_user_sessions(current_user = Depends(get_current_user)):
    """
    Returns ALL sessions for the authenticated user, sorted newest first.
    Used by Android to list all past conversations when the app is reopened.
    """
    
    db = get_database()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed.")

    user_id = str(current_user.get("user_id") or current_user.get("_id"))

    cursor = db.sessions.find({"user_id": user_id}).sort("created_at", -1)
    docs = await cursor.to_list(length=None)

    sessions = []
    for doc in docs:
        sessions.append({
            "session_id": doc.get("session_id"),
            "user_id": doc.get("user_id", user_id),
            "is_active": doc.get("is_active", False),
            "is_escalated": doc.get("is_escalated", False),
            "created_at": doc.get("created_at").replace(tzinfo=timezone.utc) if doc.get("created_at") else None,
            "updated_at": doc.get("updated_at").replace(tzinfo=timezone.utc) if doc.get("updated_at") else None,
        })

    formatted_sessions = [
        SessionResponse(**s)
        for s in sessions
    ]

    return SessionListResponse(
        status="success",
        user_id=user_id,
        total_sessions=len(formatted_sessions),
        sessions=formatted_sessions,
    )
