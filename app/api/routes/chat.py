"""
Chat API route.
Handles the full conversational pipeline via LangGraph.
Supports both SSE streaming and non-streaming responses.
"""

import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.schemas.request import ChatRequest
from app.api.schemas.response import ChatResponse, EmotionResult, CrisisResource
from app.core.constants import CRISIS_RESOURCES
from app.core.config import get_settings
from app.graph.graph_builder import build_graph
from app.core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["Chat"])

# Build the graph once at module level
_graph = None


def _get_graph():
    """Lazily builds and caches the LangGraph pipeline."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def _prepare_state(request: ChatRequest) -> dict:
    """Converts a ChatRequest into a LangGraph ChatState-compatible dict."""
    # Convert conversation history to simple dicts
    messages = []
    if request.conversation_history:
        for turn in request.conversation_history:
            messages.append({"role": turn.role, "content": turn.content})

    return {
        "user_message": request.message,
        "messages": messages,
        "assessment": request.assessment_results or {},
        "current_emotion": {},
        "top_emotion_label": "neutral",
        "top_emotion_score": 0.0,
        "risk_level": "LOW",
        "is_crisis": False,
        "crisis_response": None,
        "intent": "",
        "llm_response": "",
    }


def _build_response(result: dict, session_id: str = None) -> dict:
    """Builds a JSON-serializable response dict from the graph result."""
    is_crisis = result.get("is_crisis", False)
    risk_level = result.get("risk_level", "LOW")

    # Determine the response text
    if is_crisis:
        response_text = result.get("crisis_response", "")
    else:
        response_text = result.get("llm_response", "")

    # Include crisis resources for MEDIUM and HIGH risk
    crisis_resources = None

    return {
        "emotion": {
            "label": result.get("top_emotion_label", "neutral"),
            "score": result.get("top_emotion_score", 0.0),
        },
        "intent": result.get("intent", ""),
        "risk_level": risk_level,
        "response": response_text,
        "crisis_resources": crisis_resources,
        "session_id": session_id,
    }


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    SSE streaming endpoint.
    Runs the full LangGraph pipeline, then streams the response text
    chunk-by-chunk to the frontend for a typing effect.
    """
    logger.info(f"Chat Stream — New request (session={request.session_id})")
    logger.debug(f"Chat Stream — Message: '{request.message[:80]}...'")

    graph = _get_graph()
    state = _prepare_state(request)

    # Run the full graph pipeline
    result = graph.invoke(state)

    # Build the full response
    response_data = _build_response(result, request.session_id)

    async def event_generator():
        """Yields SSE events: first metadata, then response chunks."""
        # Send metadata (emotion, intent, risk) as a single event
        metadata = {
            "type": "metadata",
            "emotion": response_data["emotion"],
            "intent": response_data["intent"],
            "risk_level": response_data["risk_level"],
            "crisis_resources": response_data["crisis_resources"],
            "session_id": response_data["session_id"],
        }
        yield f"data: {json.dumps(metadata)}\n\n"

        # Stream the response text in chunks (simulate word-by-word)
        response_text = response_data["response"]
        words = response_text.split(" ")
        for i, word in enumerate(words):
            chunk = word if i == 0 else f" {word}"
            chunk_data = {"type": "chunk", "content": chunk}
            yield f"data: {json.dumps(chunk_data)}\n\n"

        # Send completion signal
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    logger.info(f"Chat Stream — Streaming response (risk={response_data['risk_level']})")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Non-streaming endpoint (fallback / API testing).
    Runs the full LangGraph pipeline and returns a single JSON response.
    """
    logger.info(f"Chat — New request (session={request.session_id})")

    graph = _get_graph()
    state = _prepare_state(request)
    result = graph.invoke(state)

    response_data = _build_response(result, request.session_id)

    logger.info(f"Chat — Response ready (risk={response_data['risk_level']})")

    return ChatResponse(
        emotion=EmotionResult(**response_data["emotion"]),
        intent=response_data["intent"],
        risk_level=response_data["risk_level"],
        response=response_data["response"],
        crisis_resources=[CrisisResource(**r) for r in response_data["crisis_resources"]]
        if response_data["crisis_resources"]
        else None,
        session_id=response_data["session_id"],
    )
