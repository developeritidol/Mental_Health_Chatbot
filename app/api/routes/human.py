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

    # Collect unique user IDs and batch-fetch their names
    user_ids = list({doc.get("user_id") for doc in docs if doc.get("user_id")})
    user_names: dict[str, tuple[str, Optional[str]]] = {}
    if user_ids:
        try:
            user_cursor = db.users.find(
                {"_id": {"$in": [ObjectId(uid) for uid in user_ids]}},
                {"_id": 1, "first_name": 1, "last_name": 1, "full_name": 1},
            )
            user_docs = await user_cursor.to_list(length=None)
            for u in user_docs:
                uid_str = str(u["_id"])
                fn = u.get("first_name") or (u.get("full_name") or "Unknown").split()[0]
                ln = u.get("last_name")
                user_names[uid_str] = (fn, ln)
        except Exception:
            pass

    sessions = []
    for doc in docs:
        uid = doc.get("user_id", "")
        fn, ln = user_names.get(uid, ("Unknown", None))
        sessions.append({
            "session_id": doc.get("session_id"),
            "user_id": uid,
            "first_name": fn,
            "last_name": ln,
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
                "assigned_counselor_id": None,
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

    close_notice = {
        "role": "system",
        "text": "The counselor has ended this session. You will be connected back to AI support.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_human": False,
        "is_system": True,
        "type": "session_closed",
    }
    # Send before removing the room so active WebSocket connections receive the notice
    for ws in list(manager.rooms.get(user_id, [])):
        try:
            await ws.send_text(json.dumps(close_notice))
        except Exception:
            pass

    if user_id in manager.rooms:
        del manager.rooms[user_id]
    if user_id in manager.has_human:
        del manager.has_human[user_id]

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
        # counselor_id → list of their active dashboard WebSocket(s)
        # Allows targeted push to a specific counselor without broadcasting to everyone
        self.counselor_ws: dict[str, list[WebSocket]] = {}
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

    async def connect_dashboard(self, ws: WebSocket, counselor_id: Optional[str] = None):
        await ws.accept()
        self.dashboard_clients.add(ws)
        if counselor_id:
            self.counselor_ws.setdefault(counselor_id, []).append(ws)
        logger.info(f"[WS] Admin dashboard connected. Total: {len(self.dashboard_clients)}")

    def disconnect_dashboard(self, ws: WebSocket, counselor_id: Optional[str] = None):
        self.dashboard_clients.discard(ws)
        if counselor_id and counselor_id in self.counselor_ws:
            self.counselor_ws[counselor_id] = [
                c for c in self.counselor_ws[counselor_id] if c is not ws
            ]
            if not self.counselor_ws[counselor_id]:
                del self.counselor_ws[counselor_id]
        logger.info(f"[WS] Admin dashboard disconnected. Total: {len(self.dashboard_clients)}")

    async def broadcast_to_dashboard(self, payload: dict):
        """Send to ALL connected dashboard clients (e.g. new escalation alert)."""
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

    async def notify_counselor(self, counselor_id: str, payload: dict) -> bool:
        """
        Send a targeted push to a specific counselor's dashboard WebSocket(s).
        Returns True if at least one message was delivered, False if the counselor
        has no active dashboard connection.
        """
        targets = self.counselor_ws.get(counselor_id, []).copy()
        if not targets:
            return False
        message = json.dumps(payload)
        delivered = False
        dead = []
        for ws in targets:
            try:
                await ws.send_text(message)
                delivered = True
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_dashboard(ws, counselor_id)
        return delivered


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
                    session_doc = await db.sessions.find_one({"session_id": session_id})
                    counselor_id = (session_doc or {}).get("assigned_counselor_id")

                    await db.sessions.update_one(
                        {"session_id": session_id},
                        {"$set": {
                            "is_escalated": False,
                            "assigned_counselor_id": None,
                            "escalation_closed_at": datetime.now(timezone.utc),
                        }}
                    )

                    # Free the counselor's capacity slot — is_online is owned by dashboard WebSocket
                    if counselor_id and counselor_id != "__routing__":
                        try:
                            await db.admins.update_one(
                                {"_id": ObjectId(counselor_id), "current_active_sessions": {"$gt": 0}},
                                {"$inc": {"current_active_sessions": -1}},
                            )
                            logger.info(f"[WATCHDOG] Freed capacity slot for counselor {counselor_id}.")
                        except Exception as e:
                            logger.error(f"[WATCHDOG] Failed to free capacity for counselor {counselor_id}: {e}")

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


# ── User-waiting notification ─────────────────────────────────────────────────

async def _notify_assigned_counselor_user_waiting(
    user_id: str, session_doc: Optional[dict]
) -> None:
    """
    When a user connects to the chat room, push a targeted real-time notification
    to the assigned counselor's dashboard WebSocket so they know the patient is
    waiting. Falls back to a broadcast if the counselor is not in counselor_ws
    (e.g. they connected before the counselor_ws dict was populated).
    """
    from app.core.config import get_settings
    _settings = get_settings()

    assigned_counselor_id: Optional[str] = None
    session_id: str = user_id

    if session_doc:
        assigned_counselor_id = session_doc.get("assigned_counselor_id")
        session_id = session_doc.get("session_id", user_id)
        # Routing may still be in progress — wait briefly and re-query
        if assigned_counselor_id in (None, "__routing__"):
            db = get_database()
            if db is not None:
                for _ in range(6):  # up to 12 seconds
                    await asyncio.sleep(2)
                    fresh = await db.sessions.find_one({"session_id": session_id})
                    cid = (fresh or {}).get("assigned_counselor_id")
                    if cid and cid != "__routing__":
                        assigned_counselor_id = cid
                        break

    ws_url = (
        f"ws://{_settings.SERVER_PUBLIC_HOST}:{_settings.SERVER_PORT}"
        f"/api/human/chat/{user_id}"
    )
    payload = {
        "type": "user_waiting_in_room",
        "user_id": user_id,
        "session_id": session_id,
        "websocket_url": ws_url,
        "message": "Your patient has connected and is waiting in the chat room.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if assigned_counselor_id and assigned_counselor_id != "__routing__":
        payload["counselor_id"] = assigned_counselor_id
        delivered = await manager.notify_counselor(assigned_counselor_id, payload)
        if delivered:
            logger.info(
                f"[NOTIFY] ✓ Targeted push sent | counselor={assigned_counselor_id}"
                f" | type=user_waiting_in_room | room={user_id}"
            )
        else:
            # Counselor's dashboard WS not in targeted registry — fall back to broadcast
            await manager.broadcast_to_dashboard(payload)
            logger.warning(
                f"[NOTIFY] ⚠  Counselor {assigned_counselor_id} not in counselor_ws — broadcast fallback used"
            )
    else:
        # No assignment yet (race condition) — broadcast so no counselor misses it
        await manager.broadcast_to_dashboard(payload)
        logger.warning(
            f"[NOTIFY] ⚠  No assigned counselor found for room={user_id} — broadcast fallback used"
        )


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
    """
    Counselor dashboard connection.
    Authenticates the counselor via ?token=<jwt>, marks them as available in
    the connection_registry and sets is_online=True in DB so the routing engine
    can find and assign them. Heartbeat keeps last_ping fresh.

    Unauthenticated connections (e.g. admin monitors) are accepted but do not
    affect counselor availability.
    """
    # 1. Extract and verify JWT — optional, dashboard degrades gracefully without it
    token = websocket.query_params.get("token", "").strip()
    counselor_id: Optional[str] = None

    dashboard_ip = websocket.client.host if websocket.client else "unknown"

    if token:
        try:
            credentials_exc = HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
            )
            token_data = await verify_token(token, credentials_exc)
            counselor_id = token_data.user_id
        except Exception:
            logger.warning(
                f"[WS DASHBOARD] ✗ REJECTED | ip={dashboard_ip} | reason=invalid token"
            )
            pass  # unrecognised token — treat as unauthenticated monitor

    await manager.connect_dashboard(websocket, counselor_id=counselor_id)

    # 2. Authenticated counselor: mark available for routing
    db = get_database()
    heartbeat_task: Optional[asyncio.Task] = None

    if counselor_id and db is not None:
        mark_counselor_connected(counselor_id)
        counselor_display = counselor_id  # fallback; overwrite if DB lookup succeeds
        try:
            await db.admins.update_one(
                {"_id": ObjectId(counselor_id)},
                {"$set": {"is_online": True, "last_ping": datetime.now(timezone.utc)}},
            )
            # Fetch name for log readability
            admin_doc = await db.admins.find_one(
                {"_id": ObjectId(counselor_id)}, {"first_name": 1, "last_name": 1}
            )
            if admin_doc:
                fn = admin_doc.get("first_name", "")
                ln = admin_doc.get("last_name", "")
                counselor_display = f"{fn} {ln}".strip() or counselor_id
            logger.info(
                f"[WS DASHBOARD] ✓ CONNECTED | counselor_id={counselor_id}"
                f" | name={counselor_display} | ip={dashboard_ip} | status=online (available for routing)"
            )
        except Exception as e:
            logger.warning(f"[WS DASHBOARD] Could not set online status for {counselor_id}: {e}")
        heartbeat_task = asyncio.create_task(_counselor_heartbeat(counselor_id))
    else:
        logger.info(f"[WS DASHBOARD] ✓ CONNECTED | role=anonymous monitor | ip={dashboard_ip}")

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        logger.info(f"[WS DASHBOARD] ✗ DISCONNECTED | counselor_id={counselor_id or 'anonymous'}")
        manager.disconnect_dashboard(websocket, counselor_id=counselor_id)
    except Exception as e:
        logger.error(
            f"[WS DASHBOARD] ✗ UNHANDLED ERROR | counselor_id={counselor_id or 'anonymous'} | error={e}",
            exc_info=True,
        )
        manager.disconnect_dashboard(websocket, counselor_id=counselor_id)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
        if counselor_id and db is not None:
            mark_counselor_disconnected(counselor_id)
            try:
                # Only go offline if not currently in an active patient session
                updated = await db.admins.find_one({"_id": ObjectId(counselor_id)})
                if updated and updated.get("current_active_sessions", 0) <= 0:
                    await db.admins.update_one(
                        {"_id": ObjectId(counselor_id)},
                        {"$set": {"is_online": False}},
                    )
                    logger.info(
                        f"[WS DASHBOARD] Counselor {counselor_id} is now OFFLINE | status=unavailable for routing"
                    )
                else:
                    active = (updated or {}).get("current_active_sessions", 0)
                    logger.info(
                        f"[WS DASHBOARD] Counselor {counselor_id} dashboard closed but stays ONLINE"
                        f" | active_sessions={active}"
                    )
            except Exception as e:
                logger.warning(f"[WS DASHBOARD] Could not clear online status for {counselor_id}: {e}")


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

    role = websocket.query_params.get("role", "user")
    counselor_name = websocket.query_params.get("counselor_name", "Crisis Support Team")
    client_ip = websocket.client.host if websocket.client else "unknown"

    if not token:
        logger.warning(
            f"[WS CHAT] ✗ REJECTED | room={user_id} | role={role} | ip={client_ip} | reason=no token"
        )
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
        logger.warning(
            f"[WS CHAT] ✗ REJECTED | room={user_id} | role={role} | ip={client_ip} | reason=invalid token"
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    authenticated_user_id = token_data.user_id

    # 3. Identity check: user URL must match JWT
    if role == "user" and authenticated_user_id != user_id:
        logger.warning(
            f"[WS CHAT] ✗ REJECTED | room={user_id} | role=user | auth_id={authenticated_user_id} | reason=identity mismatch"
        )
        # Fix 15: close before accept
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    db = get_database()
    session_doc: Optional[dict] = None
    actual_session_id = user_id

    # # 4. User path: verify active escalation exists for this user
    # if role == "user":
    #     is_escalated = False
    #     if db is not None:
    #         try:
    #             session_doc = await db.sessions.find_one(
    #                 {"user_id": user_id, "is_escalated": True},
    #                 sort=[("escalated_at", -1)],
    #             )
    #             is_escalated = bool(session_doc)
    #         except Exception as e:
    #             logger.error(f"[WS] DB error checking escalation for user {user_id}: {e}")
    #             await websocket.close(code=1011, reason="Internal server error")
    #             return

        if not is_escalated:
            logger.warning(f"[WS REJECT] Non-escalated user {user_id} attempted handoff connection.")
            # Fix 15: reject at handshake layer
            await websocket.close(code=4003)
            return

    # # 5. Counselor path: validate session assignment
    # if role == "human_counselor":
    #     if db is not None:
    #         try:
    #             session_doc = await db.sessions.find_one(
    #                 {"user_id": user_id, "is_escalated": True},
    #                 sort=[("escalated_at", -1)],
    #             )
    #         except Exception as e:
    #             logger.error(f"[WS] DB error fetching session for counselor on user {user_id}: {e}")
    #             await websocket.close(code=1011, reason="Internal server error")
    #             return

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
    if role == "human_counselor":
        logger.info(
            f"[WS CHAT] ✓ CONNECTED | role=counselor | room={user_id}"
            f" | counselor_id={authenticated_user_id} | name={counselor_name}"
        )
    else:
        logger.info(
            f"[WS CHAT] ✓ CONNECTED | role=user | room={user_id} | user_id={authenticated_user_id}"
        )

    # 7. User: start fallback timeout watchdog + notify assigned counselor
    if role == "user":
        if user_id not in manager.timeout_tasks:
            task = asyncio.create_task(_counselor_timeout_watchdog(user_id))
            manager.start_timeout_task(user_id, task)
            logger.info(
                f"[WS CHAT] ⏱  Counselor timeout watchdog started | room={user_id} | timeout={COUNSELOR_TIMEOUT_SECONDS}s"
            )

        # Push "user is now waiting" to the assigned counselor's dashboard
        asyncio.create_task(
            _notify_assigned_counselor_user_waiting(user_id, session_doc)
        )

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
            preview = text[:80] + ("..." if len(text) > 80 else "")
            logger.info(
                f"[WS CHAT] 💬 MESSAGE | room={user_id} | from={'counselor' if is_human else 'user'}"
                f" | len={len(text)} | text=\"{preview}\""
            )

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
            if db is not None and msg_session_id == user_id:
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
        if role == "human_counselor":
            logger.info(
                f"[WS CHAT] ✗ DISCONNECTED | role=counselor | room={user_id}"
                f" | counselor_id={authenticated_user_id} | name={counselor_name}"
            )
        else:
            logger.info(
                f"[WS CHAT] ✗ DISCONNECTED | role=user | room={user_id} | user_id={authenticated_user_id}"
            )
        leave_notice = {
            "role": "system",
            "text": f"{'Counselor' if role == 'human_counselor' else 'User'} has disconnected.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "is_human": False,
            "is_system": True,
        }
        await manager.broadcast(user_id, leave_notice, websocket)

    except Exception as e:
        logger.error(
            f"[WS CHAT] ✗ UNHANDLED ERROR | room={user_id} | role={role}"
            f" | user_id={authenticated_user_id} | error={e}",
            exc_info=True,
        )
        manager.disconnect(user_id, websocket)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass

    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()

        if role == "human_counselor":
            # Decrement ref-count in registry; counselor stays connected if dashboard is still open
            mark_counselor_disconnected(authenticated_user_id)

            if db is not None:
                try:
                    # Only decrement the session counter — is_online is owned by the dashboard WebSocket
                    await db.admins.update_one(
                        {"_id": ObjectId(authenticated_user_id), "current_active_sessions": {"$gt": 0}},
                        {"$inc": {"current_active_sessions": -1}},
                    )
                except Exception as e:
                    logger.error(f"[WS] Failed to clean up presence for counselor {authenticated_user_id}: {e}")

                if actual_session_id != user_id:
                    try:
                        await db.sessions.update_one(
                            {"session_id": actual_session_id},
                            {"$set": {
                                "is_escalated": False,
                                "assigned_counselor_id": None,
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
            try:
                await db.admins.update_one(
                    {"_id": ObjectId(counselor_id)},
                    {"$set": {"last_ping": datetime.now(timezone.utc)}},
                )
            except Exception as e:
                # A single DB failure must not kill the heartbeat loop — log and continue
                logger.warning(
                    f"[HEARTBEAT] DB write failed for counselor {counselor_id}: {e} — retrying next tick"
                )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"[HEARTBEAT] Stopped for counselor {counselor_id}: {e}")
