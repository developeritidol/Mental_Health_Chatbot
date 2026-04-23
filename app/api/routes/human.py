"""
Human Handoff — WebSocket Route + REST APIs
──────────────────────────────────────────────
ws://host/ws/human/{user_id}

Two parties connect to the same session_id "room":
  1. The Android user  (role = "user")
  2. The Human Admin   (role = "human_counselor")

Messages sent by either party are:
  - Saved to MongoDB so history is preserved
  - Broadcast in real-time to every other connection in the room

REST APIs for Admin Dashboard:
  GET  /ws/escalated                           — list all escalated sessions
  GET  /ws/escalated/{user_id}/messages        — read history before joining
  POST /ws/escalated/{user_id}/close           — end the session, return to AI
"""

import json
import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends, status
from app.core.auth.JWTtoken import verify_token
from app.services.db_service import (
    save_message,
    get_escalated_sessions,
    get_user_messages,     
    close_escalation,
    close_escalation_by_user,
    get_existing_session,
)
from app.api.schemas.response import (
    EscalatedSessionListResponse,
    EscalatedSessionResponse,
    ChatHistoryResponse,
    ChatMessageResponse,
)
from app.core.logger import get_logger
from app.core.auth.oauth2 import get_current_user

logger = get_logger(__name__)

router = APIRouter(prefix="/api/human", tags=["human"])

# ── Fallback timeout (seconds) ────────────────────────────────────────────────
COUNSELOR_TIMEOUT_SECONDS = 1200  # 20 minutes


# ── REST APIs for Human Admin Dashboard ───────────────────────────────────────

@router.get("/escalated", response_model=EscalatedSessionListResponse)
async def list_escalated_sessions(user_id: Optional[str] = None, current_provider = Depends(get_current_user)):
    """ 
    Returns all sessions that have been flagged for human intervention.
    Used by the Admin Dashboard to show the queue of users needing help.
    """
    from app.core.database import get_database
    db = get_database()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed.")
        
    query = {"is_escalated": True}
    if user_id:
        query["user_id"] = user_id
        
    cursor = db.sessions.find(query).sort("escalated_at", -1)
    docs = await cursor.to_list(length=None)
    
    sessions = []
    for doc in docs:
        sessions.append({
            "session_id": doc.get("session_id"),
            "user_id": doc.get("user_id"),
            "is_active": doc.get("is_active", True),
            "is_escalated": doc.get("is_escalated", True),
            "lethality_alert": doc.get("lethality_alert", False),
            "created_at": doc.get("created_at").replace(tzinfo=timezone.utc) if doc.get("created_at") else None,
            "updated_at": doc.get("updated_at").replace(tzinfo=timezone.utc) if doc.get("updated_at") else None,
            "escalated_at": doc.get("escalated_at").replace(tzinfo=timezone.utc) if doc.get("escalated_at") else None,
        })

    formatted = [EscalatedSessionResponse(**s) for s in sessions]
    return EscalatedSessionListResponse(
        status="success",
        total=len(formatted),
        sessions=formatted,
    )


@router.get("/escalated/{user_id}/messages", response_model=ChatHistoryResponse)
async def get_escalated_session_messages(user_id: str, current_provider = Depends(get_current_user)):
    """
    Returns the full chat history for a specific escalated session.
    Allows the human counselor to read the conversation context
    before joining the WebSocket to start the live chat.
    """
    if not user_id.strip():
        raise HTTPException(status_code=400, detail="User ID is required.")

    from app.core.database import get_database
    db = get_database()
    if not db:
        raise HTTPException(status_code=500, detail="Database connection failed.")

    session_info = await db.sessions.find_one({"user_id": user_id}, sort=[("created_at", -1)])
    if not session_info:
        messages = []
    else:
        cursor = db.messages.find({"session_id": session_info["session_id"]}).sort("timestamp", 1)
        docs = await cursor.to_list(length=None)
        
        messages = []
        for doc in docs:
            if doc.get("content"):
                messages.append({
                    "session_id": doc.get("session_id", "unknown"),
                    "role": doc.get("role", "unknown"),
                    "content": doc.get("content", ""),
                    "timestamp": doc.get("timestamp").replace(tzinfo=timezone.utc) if doc.get("timestamp") else None,
                    "user_id": user_id # map user_id back for existing schema support
                })

    formatted = [ChatMessageResponse(**msg) for msg in messages]

    return ChatHistoryResponse(
        status="success",
        user_id=user_id,
        total_messages=len(formatted),
        messages=formatted,
    )


@router.post("/escalated/{user_id}/close")
async def close_escalated_session(user_id: str, current_provider = Depends(get_current_user)):
    """
    Called by the human counselor to end the intervention.
    Flips is_escalated = False so the user's next message 
    goes back to the AI automatically.
    """
    if not user_id.strip():
        raise HTTPException(status_code=400, detail="User ID is required.")

    from app.core.database import get_database
    db = get_database()
    if not db:
        raise HTTPException(status_code=500, detail="Database connection failed.")

    try:
        await db.sessions.update_many(
            {"user_id": user_id, "is_escalated": True},
            {"$set": {
                "is_escalated": False,
                "escalation_closed_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }}
        )
        success = True
    except Exception as e:
        logger.error(f"Failed to close escalation for user {user_id}: {e}")
        success = False

    if not success:
        raise HTTPException(status_code=500, detail="Failed to close escalation.")

    # Cancel any pending counselor fallback timeouts since the session is closed
    manager.cancel_timeout_task(user_id)

    # Clear user from ConnectionManager memory
    if user_id in manager.rooms:
        del manager.rooms[user_id]
    if user_id in manager.has_human:
        del manager.has_human[user_id]
    logger.info(f"[MEMORY CLEANUP] Cleared user {user_id} from ConnectionManager memory")

    # Notify everyone in the WebSocket room that the session is ending
    close_notice = {
        "role": "system",
        "text": "The counselor has ended this session. You will be connected back to AI support.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_human": False,
        "is_system": True,
        "type": "session_closed",
    }
    for ws in manager.rooms.get(user_id, []):
        try:
            await ws.send_text(json.dumps(close_notice))
        except Exception:
            pass

    return {
        "status": "success",
        "user_id": user_id,
        "message": "Escalation closed. User will return to AI on next message.",
    }


# ── Connection Manager ────────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        # { user_id: [WebSocket, ...] }
        self.rooms: dict[str, list[WebSocket]] = {}
        # Track whether a human counselor has joined a room
        self.has_human: dict[str, bool] = {}
        # Admin dashboard connections listening for new escalations
        self.dashboard_clients: set[WebSocket] = set()
        # Track counselor fallback timeout tasks
        self.timeout_tasks: dict[str, asyncio.Task] = {}

    def start_timeout_task(self, user_id: str, task: asyncio.Task):
        self.timeout_tasks[user_id] = task

    def cancel_timeout_task(self, user_id: str):
        task = self.timeout_tasks.pop(user_id, None)
        if task: 
            task.cancel()

    def remove_timeout_task(self, user_id: str):
        if user_id in self.timeout_tasks:
            del self.timeout_tasks[user_id]

    async def connect(self, user_id: str, ws: WebSocket):
        await ws.accept()
        self.rooms.setdefault(user_id, []).append(ws)
        logger.info(f"[WS] New connection in room '{user_id}'. Total: {len(self.rooms[user_id])}")

    def mark_human_joined(self, user_id: str):
        self.has_human[user_id] = True

    def human_has_joined(self, user_id: str) -> bool:
        return self.has_human.get(user_id, False)

    def disconnect(self, user_id: str, ws: WebSocket):
        if user_id in self.rooms:
            self.rooms[user_id] = [c for c in self.rooms[user_id] if c is not ws]
            if not self.rooms[user_id]:
                del self.rooms[user_id]
                self.has_human.pop(user_id, None)
        logger.info(f"[WS] Connection closed from room '{user_id}'.")

    async def broadcast(self, user_id: str, payload: dict, sender_ws: WebSocket):
        message = json.dumps(payload)
        dead = []
        for ws in self.rooms.get(user_id, []):
            if ws is sender_ws:
                continue
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(user_id, ws)

    async def send_to_all(self, user_id: str, payload: dict):
        message = json.dumps(payload)
        ws_list = self.rooms.get(user_id, []).copy()
        for ws in ws_list:
            try:
                await ws.send_text(message)
                await ws.close()
            except Exception:
                pass
            self.disconnect(user_id, ws)

    async def connect_dashboard(self, ws: WebSocket):
        await ws.accept()
        self.dashboard_clients.add(ws)
        logger.info(f"[WS] Admin dashboard connected. Total: {len(self.dashboard_clients)}")

    def disconnect_dashboard(self, ws: WebSocket):
        self.dashboard_clients.discard(ws)
        logger.info(f"[WS] Admin dashboard disconnected. Total: {len(self.dashboard_clients)}")

    async def broadcast_to_dashboard(self, payload: dict):
        if not self.dashboard_clients:
            return
        message = json.dumps(payload)
        dead = set()
        for ws in self.dashboard_clients:
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect_dashboard(ws)


manager = ConnectionManager()


# ── Counselor Fallback Timer ──────────────────────────────────────────────────

async def _counselor_timeout_watchdog(user_id: str):
    """
    Runs as a background task after a user connects.
    If no human counselor joins within the timeout period, sends a fallback 
    message with crisis helpline info and re-enables AI on the session.
    """
    try:
        await asyncio.sleep(COUNSELOR_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return
    finally:
        manager.remove_timeout_task(user_id)

    if manager.human_has_joined(user_id):
        return

    logger.warning(f"[TIMEOUT] No counselor joined room '{user_id}' within {COUNSELOR_TIMEOUT_SECONDS}s. Sending fallback.")

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
    await manager.send_to_all(user_id, fallback)

    from app.core.database import get_database
    db = get_database()
    actual_session_id = user_id
    if db:
        session_info = await db.sessions.find_one({"user_id": user_id}, sort=[("created_at", -1)])
        if session_info:
            actual_session_id = session_info["session_id"]

    await save_message({
        "session_id": actual_session_id,
        "role": "system",
        "content": fallback["text"],
        "user_id": user_id,
    })

    if db:
        await db.sessions.update_many(
            {"user_id": user_id, "is_escalated": True},
            {"$set": {"is_escalated": False, "escalation_closed_at": datetime.now(timezone.utc)}}
        )
    logger.info(f"[TIMEOUT] User '{user_id}' returned to AI mode after timeout.")


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
            
            for expired_info in expired_sessions:
                session_id = expired_info["session_id"]
                user_id = expired_info.get("user_id", "unknown")
                
                logger.warning(f"[WATCHDOG] Session '{session_id}' (User '{user_id}') inactive for 35 mins. Closing.")
                
                from app.core.database import get_database
                db = get_database()
                if db:
                    await db.sessions.update_one(
                        {"session_id": session_id},
                        {"$set": {"is_escalated": False, "escalation_closed_at": datetime.now(timezone.utc)}}
                    )
                
                timeout_msg = {
                    "role": "system",
                    "text": "This live session has been closed due to inactivity. You will now be returned to AI support.",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "is_human": False,
                    "is_system": True,
                    "type": "session_inactive"
                }

                # Save the system message in DB
                await save_message({
                    "session_id": session_id,
                    "role": "system",
                    "content": timeout_msg["text"],
                    "user_id": user_id,
                })

                await manager.send_to_all(user_id, timeout_msg)

        except Exception as e:
            logger.error(f"[WATCHDOG] Error in loop: {e}")


# ── WebSocket Endpoint ────────────────────────────────────────────────────────

@router.websocket("/escalated/ws")
async def dashboard_notifications_ws(websocket: WebSocket):
    """
    Global WebSocket for the Admin Dashboard.
    Listens for real-time events like 'new_escalation' so the frontend
    can dynamically update without a full page reload.
    """
    await manager.connect_dashboard(websocket)
    try:
        while True:
            # Keep connection alive; clients mostly just listen
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect_dashboard(websocket)
    except Exception as e:
        logger.error(f"[WS DASHBOARD] Error: {e}")
        manager.disconnect_dashboard(websocket)


@router.websocket("/chat/{user_id}")
async def human_chat_ws(websocket: WebSocket, user_id: str, token: str):
    """
    Real-time human handoff endpoint.

    Query params accepted (optional):
      ?role=user            → for the Android user
      ?role=human_counselor → for the admin dashboard
      ?counselor_name=...   → custom name shown in Android UI
    """
    # 1. Define the exception for invalid tokens
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # 2. Verify the token BEFORE accepting the websocket connection
    try:
        token_data = verify_token(token, credentials_exception)
    except HTTPException:
        # Reject the connection immediately if token is invalid or missing
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    role = websocket.query_params.get("role", "user")
    counselor_name = websocket.query_params.get("counselor_name", "Crisis Support Team")

    # 1. Reject users if the device is not currently escalated
    if role == "user":
        from app.core.database import get_database
        db = get_database()
        is_escalated = False
        if db:
            doc = await db.sessions.find_one({"user_id": user_id, "is_escalated": True})
            is_escalated = bool(doc)
            
        if not is_escalated:
            logger.warning(f"[WS REJECT] Unauthorized connection attempt from non-escalated user: {user_id}")
            await websocket.accept()
            await websocket.send_json({
                "type": "error",
                "message": "This user is not authorized for human handoff."
            })
            await websocket.close(code=4003)
            return

    # 3. If token is valid, proceed with connection
    await manager.connect(user_id, websocket)
    logger.info(f"[WS] Role '{role}' joined room '{user_id}'")

    # If the user connects, start the fallback timer if it hasn't been started yet
    if role == "user":
        if user_id not in manager.timeout_tasks:
            task = asyncio.create_task(_counselor_timeout_watchdog(user_id))
            manager.start_timeout_task(user_id, task)
            logger.info(f"[WS] Started {COUNSELOR_TIMEOUT_SECONDS}s counselor timeout for room '{user_id}'")

    # If a human counselor joins, mark it and broadcast a system message
    if role == "human_counselor":
        manager.mark_human_joined(user_id)
        manager.cancel_timeout_task(user_id)
        join_notice = {
            "role": "human_counselor",
            "counselor_name": counselor_name,
            "text": f"{counselor_name} has joined the chat. You're not alone.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "is_human": True,
            "is_system": True,
        }
        await manager.broadcast(user_id, join_notice, websocket)

    try:
        while True:
            raw = await websocket.receive_text()

            # Parse the incoming message
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                if raw.strip().lower() == "ping":
                    await websocket.send_text("pong")
                    continue
                await websocket.send_text(json.dumps({"error": "Invalid JSON"}))
                continue

            if data.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            text = data.get("text", "").strip()
            if not text:
                continue

            is_human = (role == "human_counselor")

            # Build the broadcast payload
            payload = {
                "type": "message",
                "role": role,
                "counselor_name": counselor_name if is_human else None,
                "text": text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "is_human": is_human,
                "done": True,
            }

            from app.core.database import get_database
            db = get_database()
            actual_session_id = user_id
            if db:
                session_info = await db.sessions.find_one({"user_id": user_id}, sort=[("created_at", -1)])
                if session_info:
                    actual_session_id = session_info["session_id"]

            await save_message({
                "session_id": actual_session_id,
                "role": role,
                "content": text,
                "user_id": user_id,       # New architectural addition
                "is_human_message": is_human,
            })

            await manager.broadcast(user_id, payload, websocket)

            # Echo confirmation back to sender
            ack = {**payload, "sent": True}
            await websocket.send_text(json.dumps(ack))

    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)
        leave_notice = {
            "role": "system",
            "text": f"{'Counselor' if role == 'human_counselor' else 'User'} has disconnected.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "is_human": False,
            "is_system": True,
        }
        await manager.broadcast(user_id, leave_notice, websocket)
