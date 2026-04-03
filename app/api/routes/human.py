"""
Human Handoff — WebSocket Route + REST APIs
──────────────────────────────────────────────
ws://host/ws/human/{device_id}

Two parties connect to the same session_id "room":
  1. The Android user  (role = "user")
  2. The Human Admin   (role = "human_counselor")

Messages sent by either party are:
  - Saved to MongoDB so history is preserved
  - Broadcast in real-time to every other connection in the room

REST APIs for Admin Dashboard:
  GET  /ws/escalated                           — list all escalated sessions
  GET  /ws/escalated/{device_id}/messages     — read history before joining
  POST /ws/escalated/{device_id}/close        — end the session, return to AI
"""

import json
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from app.services.db_service import (
    save_message,
    get_escalated_sessions,
    get_device_messages,
    close_escalation_by_device,
    get_existing_session,
)
from app.api.schemas.response import (
    EscalatedSessionListResponse,
    EscalatedSessionResponse,
    ChatHistoryResponse,
    ChatMessageResponse,
)
from app.core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/human", tags=["human"])

# ── Fallback timeout (seconds) ────────────────────────────────────────────────
# If no human counselor joins within this time, the system sends a fallback
# message with crisis helpline info and re-enables AI on the session.
COUNSELOR_TIMEOUT_SECONDS = 180  # 3 minutes


# ── REST APIs for Human Admin Dashboard ───────────────────────────────────────

@router.get("/escalated", response_model=EscalatedSessionListResponse)
async def list_escalated_sessions():
    """
    Returns all sessions that have been flagged for human intervention.
    Used by the Admin Dashboard to show the queue of users needing help.
    """
    sessions = await get_escalated_sessions()
    formatted = [EscalatedSessionResponse(**s) for s in sessions]
    return EscalatedSessionListResponse(
        status="success",
        total=len(formatted),
        sessions=formatted,
    )


@router.get("/escalated/{device_id}/messages", response_model=ChatHistoryResponse)
async def get_escalated_session_messages(device_id: str):
    """
    Returns the full chat history for a specific escalated session.
    Allows the human counselor to read the conversation context
    before joining the WebSocket to start the live chat.
    """
    if not device_id.strip():
        raise HTTPException(status_code=400, detail="Device ID is required.")

    messages = await get_device_messages(device_id)
    formatted = [ChatMessageResponse(**msg) for msg in messages]

    return ChatHistoryResponse(
        status="success",
        device_id=device_id,
        total_messages=len(formatted),
        messages=formatted,
    )


@router.post("/escalated/{device_id}/close")
async def close_escalated_session(device_id: str):
    """
    Called by the human counselor to end the intervention.
    Flips is_escalated = False so the user's next message 
    goes back to the AI automatically.
    """
    if not device_id.strip():
        raise HTTPException(status_code=400, detail="Device ID is required.")

    success = await close_escalation_by_device(device_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to close escalation.")

    # Notify everyone in the WebSocket room that the session is ending
    close_notice = {
        "role": "system",
        "text": "The counselor has ended this session. You will be connected back to AI support.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_human": False,
        "is_system": True,
        "type": "session_closed",
    }
    for ws in manager.rooms.get(device_id, []):
        try:
            await ws.send_text(json.dumps(close_notice))
        except Exception:
            pass

    return {
        "status": "success",
        "device_id": device_id,
        "message": "Escalation closed. User will return to AI on next message.",
    }


# ── Connection Manager ────────────────────────────────────────────────────────
# Maps device_id → list of active WebSocket connections.
# Kept in-memory; perfectly fine for a single server instance.

class ConnectionManager:
    def __init__(self):
        # { device_id: [WebSocket, ...] }
        self.rooms: dict[str, list[WebSocket]] = {}
        # Track whether a human counselor has joined a room
        self.has_human: dict[str, bool] = {}

    async def connect(self, device_id: str, ws: WebSocket):
        await ws.accept()
        self.rooms.setdefault(device_id, []).append(ws)
        logger.info(f"[WS] New connection in room '{device_id}'. Total: {len(self.rooms[device_id])}")

    def mark_human_joined(self, device_id: str):
        self.has_human[device_id] = True

    def human_has_joined(self, device_id: str) -> bool:
        return self.has_human.get(device_id, False)

    def disconnect(self, device_id: str, ws: WebSocket):
        if device_id in self.rooms:
            self.rooms[device_id] = [c for c in self.rooms[device_id] if c is not ws]
            if not self.rooms[device_id]:
                del self.rooms[device_id]
                self.has_human.pop(device_id, None)
        logger.info(f"[WS] Connection closed from room '{device_id}'.")

    async def broadcast(self, device_id: str, payload: dict, sender_ws: WebSocket):
        """Send JSON payload to ALL other parties in the room."""
        message = json.dumps(payload)
        dead = []
        for ws in self.rooms.get(device_id, []):
            if ws is sender_ws:
                continue  # don't echo back to the sender
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        # clean up dead connections
        for ws in dead:
            self.disconnect(device_id, ws)

    async def send_to_all(self, device_id: str, payload: dict):
        """Forcefully sends a JSON message to ALL parties in a room & drops them."""
        message = json.dumps(payload)
        for ws in self.rooms.get(device_id, []):
            try:
                await ws.send_text(message)
                await ws.close()
            except Exception:
                pass
        self.disconnect(device_id, ws)


manager = ConnectionManager()


# ── 60-Second Fallback Timer ──────────────────────────────────────────────────

async def _counselor_timeout_watchdog(device_id: str):
    """
    Runs as a background task after a user connects.
    If no human counselor joins within 60 seconds, sends a fallback 
    message with crisis helpline info and re-enables AI on the session.
    """
    await asyncio.sleep(COUNSELOR_TIMEOUT_SECONDS)

    # Check if a human counselor joined during the wait
    if manager.human_has_joined(device_id):
        return  # counselor joined in time, nothing to do

    logger.warning(f"[TIMEOUT] No counselor joined room '{device_id}' within {COUNSELOR_TIMEOUT_SECONDS}s. Sending fallback.")

    # Send fallback message to the user
    fallback = {
        "role": "system",
        "text": (
            "Our crisis counselors are currently unavailable. "
            "If you are in immediate danger, please call the crisis helpline: "
            "988 (Suicide & Crisis Lifeline) or 112 (Emergency). "
            "I'll stay with you and continue our conversation."
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_human": False,
        "is_system": True,
        "type": "counselor_unavailable",
    }
    await manager.send_to_all(device_id, fallback)

    # Attempt to resolve the real session_id by looking up the device
    session_info = await get_existing_session(device_id)
    actual_session_id = session_info["session_id"] if session_info else device_id

    # Save the fallback message in DB so it's part of chat history
    await save_message({
        "session_id": actual_session_id,
        "role": "system",
        "content": fallback["text"],
        "device_id": device_id,
    })

    # Re-enable AI on this session so the user isn't stuck
    await close_escalation_by_device(device_id)
    logger.info(f"[TIMEOUT] Device '{device_id}' returned to AI mode after timeout.")


# ── Global 35-Minute Inactivity Watchdog ──────────────────────────────────────

async def inactivity_watchdog():
    """
    Runs globally in the background every 60 seconds.
    Finds escalated sessions that haven't received a message in 35 minutes.
    Closes the escalation, returns the user to AI, and forces the WebSocket tab to close.
    """
    from app.services.db_service import get_expired_escalated_sessions
    logger.info("[WATCHDOG] Started 35-minute inactivity watchdog.")
    
    while True:
        try:
            # Check every 60 seconds
            await asyncio.sleep(60)
            
            # Fetch sessions inactive for 35+ minutes
            expired_sessions = await get_expired_escalated_sessions(timeout_minutes=35)
            
            for device_id in expired_sessions:
                logger.warning(f"[WATCHDOG] Device '{device_id}' inactive for 35 mins. Closing.")
                
                # Close the escalation (sets is_escalated = False)
                success = await close_escalation_by_device(device_id)
                if not success:
                    continue
                
                # Notify the user
                timeout_msg = {
                    "role": "system",
                    "text": "This live session has been closed due to inactivity. You will now be returned to AI support.",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "is_human": False,
                    "is_system": True,
                    "type": "session_inactive"
                }

                # Attempt to resolve the real session_id by looking up the device
                session_info = await get_existing_session(device_id)
                actual_session_id = session_info["session_id"] if session_info else device_id

                # Save the system message in DB
                await save_message({
                    "session_id": actual_session_id,
                    "role": "system",
                    "content": timeout_msg["text"],
                    "device_id": "system",
                })

                # Broadcast over websocket to force UI close, then drop connections
                await manager.send_to_all(device_id, timeout_msg)

        except Exception as e:
            logger.error(f"[WATCHDOG] Error in loop: {e}")


# ── WebSocket Endpoint ────────────────────────────────────────────────────────

@router.websocket("/chat/{device_id}")
async def human_chat_ws(websocket: WebSocket, device_id: str):
    """
    Real-time human handoff endpoint.

    Query params accepted (optional):
      ?role=user            → for the Android user
      ?role=human_counselor → for the admin dashboard
      ?counselor_name=...   → custom name shown in Android UI
    """
    role = websocket.query_params.get("role", "user")
    counselor_name = websocket.query_params.get("counselor_name", "Crisis Support Team")

    # 1. Reject users if the device is not currently escalated
    if role == "user":
        from app.services.db_service import is_device_escalated
        if not await is_device_escalated(device_id):
            logger.warning(f"[WS REJECT] Unauthorized connection attempt from non-escalated device: {device_id}")
            await websocket.accept() # Must accept before closing with custom code often
            await websocket.send_json({
                "type": "error",
                "message": "This device is not authorized for human handoff."
            })
            await websocket.close(code=4003)
            return

    await manager.connect(device_id, websocket)
    logger.info(f"[WS] Role '{role}' joined room '{device_id}'")

    # If the user connects, start the 60-second fallback timer
    if role == "user":
        asyncio.create_task(_counselor_timeout_watchdog(device_id))
        logger.info(f"[WS] Started {COUNSELOR_TIMEOUT_SECONDS}s counselor timeout for room '{device_id}'")

    # If a human counselor joins, mark it and broadcast a system message
    if role == "human_counselor":
        manager.mark_human_joined(device_id)
        join_notice = {
            "role": "human_counselor",
            "counselor_name": counselor_name,
            "text": f"{counselor_name} has joined the chat. You're not alone.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "is_human": True,
            "is_system": True,
        }
        await manager.broadcast(device_id, join_notice, websocket)

    try:
        while True:
            raw = await websocket.receive_text()

            # Parse the incoming message
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"error": "Invalid JSON"})
                )
                continue

            text = data.get("text", "").strip()
            if not text:
                continue

            is_human = (role == "human_counselor")

            # Build the broadcast payload
            payload = {
                "role": role,
                "counselor_name": counselor_name if is_human else None,
                "text": text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "is_human": is_human,
            }

            # Attempt to resolve the real session_id by looking up the device
            session_info = await get_existing_session(device_id)
            actual_session_id = session_info["session_id"] if session_info else device_id

            # Persist message in MongoDB (same collection as AI chat)
            await save_message({
                "session_id": actual_session_id,
                "role": role,
                "content": text,
                "device_id": device_id,
                "is_human_message": is_human,
            })

            # Broadcast to the other party in real-time
            await manager.broadcast(device_id, payload, websocket)

            # Echo confirmation back to sender
            ack = {**payload, "sent": True}
            await websocket.send_text(json.dumps(ack))

    except WebSocketDisconnect:
        manager.disconnect(device_id, websocket)
        # Notify the other party that this person left
        leave_notice = {
            "role": "system",
            "text": f"{'Counselor' if role == 'human_counselor' else 'User'} has disconnected.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "is_human": False,
            "is_system": True,
        }
        await manager.broadcast(device_id, leave_notice, websocket)
