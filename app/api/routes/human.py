"""
Human Handoff — WebSocket Route + REST APIs
──────────────────────────────────────────────
ws://host/api/human/chat/{user_id}

Two parties connect to the same user_id "room":
  1. The Android user  (role = "user")
  2. The Human Counselor (role = "human_counselor")

Fix 9:  confirmed counselor ID written to session on WebSocket join.
Fix 11: placeholder handoff brief sent immediately; background task delivers
        the real summary once GPT-4o finishes (no hard 5-second deadline).
Fix 12: mark_counselor_connected/disconnected maintain the connection registry.
Fix 13: cancel_timeout_task called unconditionally in disconnect() to prevent
        orphaned asyncio task leaks.
Fix 15: unauthorized connections closed before websocket.accept() is called.
"""

import json
import asyncio
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends, status
from app.core.auth.JWTtoken import verify_token
from app.core.database import get_database
from app.core.connection_registry import mark_counselor_connected, mark_counselor_disconnected
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

COUNSELOR_TIMEOUT_SECONDS = 1200  # 20 minutes


# ── REST APIs ─────────────────────────────────────────────────────────────────

@router.get("/escalated", response_model=EscalatedSessionListResponse)
async def list_escalated_sessions(user_id: Optional[str] = None, current_provider = Depends(get_current_user)):
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
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
            "escalated_at": doc.get("escalated_at"),
        })

    formatted = [EscalatedSessionResponse(**s) for s in sessions]
    return EscalatedSessionListResponse(status="success", total=len(formatted), sessions=formatted)


@router.get("/escalated/{user_id}/messages", response_model=ChatHistoryResponse)
async def get_escalated_session_messages(user_id: str, current_provider = Depends(get_current_user)):
    if not user_id.strip():
        raise HTTPException(status_code=400, detail="User ID is required.")

    db = get_database()
    if db is None:
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
                    "user_id": user_id,
                })

    formatted = [ChatMessageResponse(**msg) for msg in messages]
    return ChatHistoryResponse(status="success", user_id=user_id, total_messages=len(formatted), messages=formatted)


@router.post("/escalated/{user_id}/close")
async def close_escalated_session(user_id: str, current_provider = Depends(get_current_user)):
    if not user_id.strip():
        raise HTTPException(status_code=400, detail="User ID is required.")

    db = get_database()
    if db is None:
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

    manager.cancel_timeout_task(user_id)

    if user_id in manager.rooms:
        del manager.rooms[user_id]
    if user_id in manager.has_human:
        del manager.has_human[user_id]

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
        self.rooms: dict[str, list[WebSocket]] = {}
        self.has_human: dict[str, bool] = {}
        self.dashboard_clients: set[WebSocket] = set()
        self.timeout_tasks: dict[str, asyncio.Task] = {}

    def start_timeout_task(self, user_id: str, task: asyncio.Task):
        self.timeout_tasks[user_id] = task

    def cancel_timeout_task(self, user_id: str):
        task = self.timeout_tasks.pop(user_id, None)
        if task:
            task.cancel()

    def remove_timeout_task(self, user_id: str):
        self.timeout_tasks.pop(user_id, None)

    async def connect(self, user_id: str, ws: WebSocket):
        await ws.accept()
        self.rooms.setdefault(user_id, []).append(ws)
        logger.info(f"[WS] New connection in room '{user_id}'. Total: {len(self.rooms[user_id])}")

    def mark_human_joined(self, user_id: str):
        self.has_human[user_id] = True

    def human_has_joined(self, user_id: str) -> bool:
        return self.has_human.get(user_id, False)

    def disconnect(self, user_id: str, ws: WebSocket):
        # Fix 13: cancel unconditionally — timeout tasks created before room registration
        # (or when the room still has other connections) would otherwise leak.
        self.cancel_timeout_task(user_id)

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
    try:
        await asyncio.sleep(COUNSELOR_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return
    finally:
        manager.remove_timeout_task(user_id)

    if manager.human_has_joined(user_id):
        return

    logger.warning(f"[TIMEOUT] No counselor joined room '{user_id}' within {COUNSELOR_TIMEOUT_SECONDS}s.")

    db = get_database()
    actual_session_id = user_id
    session_doc = None

    if db is not None:
        session_doc = await db.sessions.find_one({"user_id": user_id, "is_escalated": True})
        if session_doc:
            actual_session_id = session_doc.get("session_id", user_id)

    if db is not None and session_doc is not None:
        failed_counselor_id = session_doc.get("assigned_counselor_id")
        await db.sessions.update_one(
            {"session_id": actual_session_id},
            {"$set": {"assigned_counselor_id": None}},
        )

        from app.services.routing_service import route_crisis_session
        crisis_category = session_doc.get("crisis_category", "unknown")
        logger.info(f"[TIMEOUT] Re-routing session {actual_session_id}, excluding {failed_counselor_id}.")
        reroute_consensus: dict = {"category": crisis_category, "is_crisis": True}
        if failed_counselor_id:
            reroute_consensus["_exclude_counselor_id"] = failed_counselor_id
        asyncio.create_task(
            route_crisis_session(user_id=user_id, session_id=actual_session_id, consensus=reroute_consensus)
        )
        return

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
    await save_message({"session_id": actual_session_id, "role": "system", "content": fallback["text"], "user_id": user_id})

    if db is not None:
        await db.sessions.update_many(
            {"user_id": user_id, "is_escalated": True},
            {"$set": {"is_escalated": False, "escalation_closed_at": datetime.now(timezone.utc)}},
        )
    logger.info(f"[TIMEOUT] User '{user_id}' returned to AI mode after timeout.")


# ── Global 35-Minute Inactivity Watchdog ──────────────────────────────────────

async def inactivity_watchdog():
    from app.services.db_service import get_expired_escalated_sessions
    logger.info("[WATCHDOG] Started 35-minute inactivity watchdog.")

    while True:
        try:
            await asyncio.sleep(60)
            expired_sessions = await get_expired_escalated_sessions(timeout_minutes=35)

            for expired_info in expired_sessions:
                session_id = expired_info["session_id"]
                user_id = expired_info.get("user_id", "unknown")
                logger.warning(f"[WATCHDOG] Session '{session_id}' (User '{user_id}') inactive for 35 mins. Closing.")

                db = get_database()
                if db is not None:
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
                    "type": "session_inactive",
                }
                await save_message({"session_id": session_id, "role": "system", "content": timeout_msg["text"], "user_id": user_id})
                await manager.send_to_all(user_id, timeout_msg)

        except Exception as e:
            logger.error(f"[WATCHDOG] Error in loop: {e}")


# ── Handoff Summary Background Delivery (Fix 11) ─────────────────────────────

async def _deliver_handoff_when_ready(ws: WebSocket, session_id: str, db) -> None:
    """
    Fix 11: delivers the GPT-4o handoff summary to the counselor's WebSocket
    as soon as it is written to the DB, regardless of how long generation takes.
    Polls every 10 seconds for up to 5 minutes before giving up.
    """
    for _ in range(30):  # 30 × 10s = 5 minutes max
        await asyncio.sleep(10)
        try:
            doc = await db.sessions.find_one({"session_id": session_id})
            summary = (doc or {}).get("handoff_summary")
            if summary:
                await ws.send_json({
                    "type": "system_handoff_brief_ready",
                    "content": summary,
                    "crisis_category": (doc or {}).get("crisis_category", "unknown"),
                    "summary_ready": True,
                })
                logger.info(f"[WS] Delayed handoff summary delivered for session {session_id}.")
                return
        except Exception:
            return  # WebSocket closed or DB error — stop silently


# ── Dashboard WebSocket ───────────────────────────────────────────────────────

@router.websocket("/escalated/ws")
async def dashboard_notifications_ws(websocket: WebSocket):
    await manager.connect_dashboard(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect_dashboard(websocket)
    except Exception as e:
        logger.error(f"[WS DASHBOARD] Error: {e}")
        manager.disconnect_dashboard(websocket)


# ── Human Chat WebSocket ──────────────────────────────────────────────────────

@router.websocket("/chat/{user_id}")
async def human_chat_ws(websocket: WebSocket, user_id: str):
    """
    Real-time human handoff endpoint.
    Query params:
      ?token=<jwt>          token (fallback when Authorization header unavailable)
      ?role=user            Android user
      ?role=human_counselor counselor dashboard
      ?counselor_name=...   display name shown in Android UI
    """
    # 1. Extract token from header or query param
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1].strip()
    else:
        token = websocket.query_params.get("token", "").strip()

    if not token:
        # Fix 15: close at handshake layer — no accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # 2. Verify token BEFORE accepting the WebSocket handshake
    try:
        token_data = await verify_token(token, credentials_exception)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    authenticated_user_id = token_data.user_id
    role = websocket.query_params.get("role", "user")
    counselor_name = websocket.query_params.get("counselor_name", "Crisis Support Team")

    # 3. Identity check: user URL must match JWT
    if role == "user" and authenticated_user_id != user_id:
        logger.warning(f"[WS SECURITY] User {authenticated_user_id} attempted room for user {user_id}")
        # Fix 15: close before accept
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    db = get_database()
    session_doc: Optional[dict] = None
    actual_session_id = user_id

    # 4. User path: verify active escalation exists for this user
    if role == "user":
        is_escalated = False
        if db is not None:
            try:
                session_doc = await db.sessions.find_one(
                    {"user_id": user_id, "is_escalated": True},
                    sort=[("escalated_at", -1)],
                )
                is_escalated = bool(session_doc)
            except Exception as e:
                logger.error(f"[WS] DB error checking escalation for user {user_id}: {e}")
                await websocket.close(code=1011, reason="Internal server error")
                return

        if not is_escalated:
            logger.warning(f"[WS REJECT] Non-escalated user {user_id} attempted handoff connection.")
            # Fix 15: reject at handshake layer
            await websocket.close(code=4003)
            return

    # 5. Counselor path: validate session assignment
    if role == "human_counselor":
        if db is not None:
            try:
                session_doc = await db.sessions.find_one(
                    {"user_id": user_id, "is_escalated": True},
                    sort=[("escalated_at", -1)],
                )
            except Exception as e:
                logger.error(f"[WS] DB error fetching session for counselor on user {user_id}: {e}")
                await websocket.close(code=1011, reason="Internal server error")
                return

        if session_doc:
            actual_session_id = session_doc.get("session_id", user_id)
            assigned = session_doc.get("assigned_counselor_id")
            # Reject if routed to a different counselor
            if assigned and assigned not in (None, "__routing__") and assigned != authenticated_user_id:
                logger.warning(
                    f"[WS SECURITY] Counselor {authenticated_user_id} unauthorised for session "
                    f"{actual_session_id} (assigned: {assigned})."
                )
                # Fix 15: reject at handshake layer
                await websocket.close(code=4003)
                return

    # 6. Accept WebSocket and register
    await manager.connect(user_id, websocket)
    logger.info(f"[WS] Role '{role}' joined room '{user_id}'")

    # 7. User: start fallback timeout watchdog
    if role == "user":
        if user_id not in manager.timeout_tasks:
            task = asyncio.create_task(_counselor_timeout_watchdog(user_id))
            manager.start_timeout_task(user_id, task)
            logger.info(f"[WS] Started {COUNSELOR_TIMEOUT_SECONDS}s counselor timeout for room '{user_id}'")

    heartbeat_task: Optional[asyncio.Task] = None

    # 8. Counselor: update presence, write confirmed session assignment, send handoff brief
    if role == "human_counselor":
        # Fix 12: register in connection registry for routing availability check
        mark_counselor_connected(authenticated_user_id)

        if db is not None:
            try:
                await db.admins.update_one(
                    {"_id": ObjectId(authenticated_user_id)},
                    {
                        "$set": {"is_online": True, "last_ping": datetime.now(timezone.utc)},
                        "$inc": {"current_active_sessions": 1},
                    },
                )
            except Exception as e:
                logger.warning(f"[WS] Could not update presence for counselor {authenticated_user_id}: {e}")

            # Fix 9: write the confirmed counselor ID — replaces __routing__ placeholder
            if session_doc is not None:
                try:
                    await db.sessions.update_one(
                        {"session_id": actual_session_id},
                        {"$set": {"assigned_counselor_id": authenticated_user_id}},
                    )
                    logger.info(f"[WS] Confirmed counselor {authenticated_user_id} written to session {actual_session_id}.")
                except Exception as e:
                    logger.warning(f"[WS] Could not confirm counselor ID for session {actual_session_id}: {e}")

        heartbeat_task = asyncio.create_task(_counselor_heartbeat(authenticated_user_id))

        manager.mark_human_joined(user_id)
        manager.cancel_timeout_task(user_id)

        # Fix 11: send placeholder immediately; start background task to push real summary
        if db is not None and session_doc is not None:
            handoff_summary = session_doc.get("handoff_summary")

            await websocket.send_json({
                "type": "system_handoff_brief",
                "content": handoff_summary or (
                    "Clinical summary is being generated — you will receive it shortly."
                ),
                "crisis_category": session_doc.get("crisis_category", "unknown"),
                "summary_ready": bool(handoff_summary),
            })

            if handoff_summary:
                logger.info(f"[WS] Handoff brief delivered immediately to counselor {authenticated_user_id}.")
            else:
                logger.info(f"[WS] Placeholder sent; launching background delivery task for session {actual_session_id}.")
                asyncio.create_task(_deliver_handoff_when_ready(websocket, actual_session_id, db))

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

            payload = {
                "type": "message",
                "role": role,
                "counselor_name": counselor_name if is_human else None,
                "text": text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "is_human": is_human,
                "done": True,
            }

            msg_session_id = actual_session_id
            if db and msg_session_id == user_id:
                session_info = await db.sessions.find_one(
                    {"user_id": user_id}, sort=[("created_at", -1)]
                )
                if session_info:
                    msg_session_id = session_info["session_id"]

            await save_message({
                "session_id": msg_session_id,
                "role": role,
                "content": text,
                "user_id": user_id,
                "is_human_message": is_human,
            })

            await manager.broadcast(user_id, payload, websocket)
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

    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()

        if role == "human_counselor":
            # Fix 12: remove from connection registry on disconnect
            mark_counselor_disconnected(authenticated_user_id)

            if db is not None:
                try:
                    await db.admins.update_one(
                        {"_id": ObjectId(authenticated_user_id), "current_active_sessions": {"$gt": 0}},
                        {"$inc": {"current_active_sessions": -1}},
                    )
                    updated_admin = await db.admins.find_one({"_id": ObjectId(authenticated_user_id)})
                    if updated_admin and updated_admin.get("current_active_sessions", 0) <= 0:
                        await db.admins.update_one(
                            {"_id": ObjectId(authenticated_user_id)},
                            {"$set": {"is_online": False, "current_active_sessions": 0}},
                        )
                except Exception as e:
                    logger.error(f"[WS] Failed to clean up presence for counselor {authenticated_user_id}: {e}")

                if actual_session_id != user_id:
                    try:
                        await db.sessions.update_one(
                            {"session_id": actual_session_id},
                            {"$set": {
                                "is_escalated": False,
                                "escalation_closed_at": datetime.now(timezone.utc),
                                "updated_at": datetime.now(timezone.utc),
                            }},
                        )
                    except Exception as e:
                        logger.error(f"[WS] Failed to close escalation for session {actual_session_id}: {e}")


async def _counselor_heartbeat(counselor_id: str) -> None:
    """Updates last_ping every 20 seconds. Cancelled in the finally block on disconnect."""
    db = get_database()
    if db is None:
        return
    try:
        while True:
            await asyncio.sleep(20)
            await db.admins.update_one(
                {"_id": ObjectId(counselor_id)},
                {"$set": {"last_ping": datetime.now(timezone.utc)}},
            )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"[HEARTBEAT] Stopped for counselor {counselor_id}: {e}")
