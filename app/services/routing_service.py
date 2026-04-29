"""
Smart Counselor Routing Service
────────────────────────────────
Implements the 3-tier routing decision engine for crisis escalations:

  Tier 1 — Sticky Routing:    Route to the user's preferred (last trusted) counselor.
  Tier 2 — Context Match:     Verify the crisis category is compatible with past history.
  Tier 3 — Availability Gate: Confirm the counselor is online, has a fresh heartbeat,
                               has remaining capacity, AND has an active WebSocket on
                               this server process (connection_registry check).

Falls back to a pool-based search if any tier fails.
Always dispatches an LLM-generated clinical handoff summary as a background task
so the counselor is pre-briefed before the user-visible chat begins.

Fix 3:  atomic routing lock now also writes routing_started_at timestamp.
Fix 14: stale __routing__ locks older than 5 minutes are cleaned up before each
        routing attempt so a server crash cannot permanently block a session.
Fix 12: _is_available() now also checks connection_registry.is_counselor_connected()
        so a counselor with a stale is_online flag but no active WebSocket is not routed to.

Entry point: route_crisis_session() — called via asyncio.create_task() from chat.py.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId

from app.core.database import get_database
from app.core.connection_registry import is_counselor_connected
from app.services.summarization_service import generate_clinical_handoff

logger = logging.getLogger(__name__)

_STALE_PING_SECONDS = 45
_STALE_LOCK_MINUTES = 5


# ── Tier helpers ─────────────────────────────────────────────────────────────

def _is_fresh(counselor_doc: dict) -> bool:
    """Returns True if the counselor's heartbeat is recent enough to trust."""
    last_ping = counselor_doc.get("last_ping")
    if last_ping is None:
        return False
    if last_ping.tzinfo is None:
        last_ping = last_ping.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last_ping) < timedelta(seconds=_STALE_PING_SECONDS)


def _is_available(counselor_doc: dict) -> bool:
    """
    Returns True only when ALL four gates pass:
      1. is_online flag is True in DB
      2. Heartbeat is fresh (last_ping within _STALE_PING_SECONDS)
      3. Current sessions < max_concurrent_sessions
      4. Counselor has an active WebSocket on this server process (Fix 12)
    """
    counselor_id = str(counselor_doc.get("_id", ""))
    return (
        counselor_doc.get("is_online", False)
        and _is_fresh(counselor_doc)
        and counselor_doc.get("current_active_sessions", 0)
        < counselor_doc.get("max_concurrent_sessions", 3)
        and is_counselor_connected(counselor_id)
    )


def _categories_match(current: str, previous: Optional[str]) -> bool:
    if previous is None:
        return True
    return current == previous


async def _find_available_counselor(exclude_id: Optional[str] = None) -> Optional[dict]:
    """
    Pool fallback: returns the least-loaded counselor that passes all availability
    gates including an active WebSocket connection on this process.
    """
    db = get_database()
    if db is None:
        return None

    stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=_STALE_PING_SECONDS)
    query: dict = {
        "is_online": True,
        "last_ping": {"$gte": stale_cutoff},
        "$expr": {"$lt": ["$current_active_sessions", "$max_concurrent_sessions"]},
    }
    if exclude_id:
        try:
            query["_id"] = {"$ne": ObjectId(exclude_id)}
        except Exception:
            pass

    # Fetch a batch sorted by load; filter to those with active WebSockets
    cursor = db.admins.find(query).sort("current_active_sessions", 1)
    candidates = await cursor.to_list(length=20)

    for candidate in candidates:
        if is_counselor_connected(str(candidate["_id"])):
            return candidate

    return None


# ── Public entry point ────────────────────────────────────────────────────────

async def route_crisis_session(user_id: str, session_id: str, consensus: dict) -> None:
    """
    Main routing orchestrator. Always called via asyncio.create_task() so it
    never blocks the SSE stream.
    """
    db = get_database()
    if db is None:
        logger.error(f"[ROUTING] Database unavailable — cannot route session {session_id}")
        return

    crisis_category = consensus.get("category", "unknown")
    force_exclude_id: Optional[str] = consensus.get("_exclude_counselor_id")

    try:
        # Fix 14: release stale __routing__ locks from past server crashes
        # before acquiring the lock for this session.
        stale_lock_cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STALE_LOCK_MINUTES)
        await db.sessions.update_many(
            {
                "assigned_counselor_id": "__routing__",
                "routing_started_at": {"$lt": stale_lock_cutoff},
            },
            {"$set": {"assigned_counselor_id": None, "routing_started_at": None}},
        )

        # Fix 3: atomic lock writes routing_started_at so Fix 14 can calculate
        # lock age after a crash and release it automatically.
        claim_result = await db.sessions.find_one_and_update(
            {"session_id": session_id, "assigned_counselor_id": None},
            {"$set": {
                "assigned_counselor_id": "__routing__",
                "routing_started_at": datetime.now(timezone.utc),
            }},
        )
        if claim_result is None:
            logger.info(f"[ROUTING] Session {session_id} already being routed — skipping duplicate task.")
            return

        assigned_counselor: Optional[dict] = None
        preferred_id: Optional[str] = None

        # ── Tier 1: Sticky Routing ───────────────────────────────────────────
        try:
            user_doc = await db.users.find_one({"_id": ObjectId(user_id)})
        except Exception:
            user_doc = None
        if not user_doc:
            logger.error(f"[ROUTING] User {user_id} not found — releasing routing lock.")
            await db.sessions.update_one(
                {"session_id": session_id, "assigned_counselor_id": "__routing__"},
                {"$set": {"assigned_counselor_id": None, "routing_started_at": None}},
            )
            return

        preferred_id = user_doc.get("preferred_counselor_id")

        if preferred_id and preferred_id == force_exclude_id:
            preferred_id = None

        if preferred_id:
            preferred_doc = await db.admins.find_one({"_id": ObjectId(preferred_id)})
            if preferred_doc:
                # ── Tier 2: Context Match ────────────────────────────────────
                if _categories_match(crisis_category, user_doc.get("last_crisis_category")):
                    # ── Tier 3: Availability Gate ────────────────────────────
                    if _is_available(preferred_doc):
                        assigned_counselor = preferred_doc
                        logger.info(f"[ROUTING] Preferred counselor {preferred_id} selected for session {session_id}.")

        # ── Fallback: Pool Search ─────────────────────────────────────────────
        if assigned_counselor is None:
            logger.info(f"[ROUTING] Falling back to pool search for session {session_id}.")
            exclude_id = preferred_id or force_exclude_id
            assigned_counselor = await _find_available_counselor(exclude_id=exclude_id)

        # ── No counselors available ───────────────────────────────────────────
        if assigned_counselor is None:
            logger.warning(f"[ROUTING] No counselors available for session {session_id}. Serving hotline message.")
            await db.sessions.update_one(
                {"session_id": session_id},
                {"$set": {"assigned_counselor_id": None, "routing_started_at": None}},
            )
            await db.messages.insert_one({
                "session_id": session_id,
                "role": "system",
                "sender_type": "system",
                "content": (
                    "We're sorry, no counselors are available right now. "
                    "If you are in immediate danger, please call the National Crisis Helpline: 988."
                ),
                "timestamp": datetime.now(timezone.utc),
            })
            return

        counselor_id_str = str(assigned_counselor["_id"])

        # ── Persist assignment — single atomic $set (Fix 8 enhancement) ──────
        await db.sessions.update_one(
            {"session_id": session_id},
            {"$set": {
                "assigned_counselor_id": counselor_id_str,
                "crisis_category": crisis_category,
                "assigned_at": datetime.now(timezone.utc),
                "assignment_complete": True,
                "routing_started_at": None,
            }},
        )

        # ── Generate handoff summary in background ────────────────────────────
        asyncio.create_task(
            _run_summarization_and_save(user_id, session_id, crisis_category)
        )

        # ── Update user's routing profile for next escalation ─────────────────
        try:
            await db.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {
                    "preferred_counselor_id": counselor_id_str,
                    "last_crisis_category": crisis_category,
                }},
            )
        except Exception:
            logger.warning(f"[ROUTING] Could not update preferred counselor for user {user_id}.")

        logger.info(
            f"[ROUTING] Session {session_id} assigned to counselor {counselor_id_str} "
            f"(category: {crisis_category})."
        )

        await _notify_counselor(counselor_id_str, session_id, crisis_category)

    except Exception:
        logger.exception(f"[ROUTING] Unhandled error routing session {session_id}")
        try:
            await db.sessions.update_one(
                {"session_id": session_id, "assigned_counselor_id": "__routing__"},
                {"$set": {"assigned_counselor_id": None, "routing_started_at": None}},
            )
        except Exception:
            pass


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _run_summarization_and_save(
    user_id: str,
    session_id: str,
    crisis_category: str,
) -> None:
    """Generates the clinical handoff note and persists it to the session doc."""
    try:
        db = get_database()
        if db is None:
            return
        summary = await generate_clinical_handoff(user_id, session_id, crisis_category)
        await db.sessions.update_one(
            {"session_id": session_id},
            {"$set": {"handoff_summary": summary}},
        )
        logger.info(f"[ROUTING] Handoff summary saved for session {session_id}.")
    except Exception:
        logger.exception(f"[ROUTING] Summarization failed for session {session_id}.")


async def _notify_counselor(
    counselor_id: str,
    session_id: str,
    crisis_category: str,
) -> None:
    """
    Stub for counselor push notification.
    Replace with FCM, counselor dashboard WebSocket ping, or an internal
    signal channel for your infrastructure.
    """
    logger.info(
        f"[ROUTING] [NOTIFY] Counselor {counselor_id} paged for "
        f"session {session_id} (category: {crisis_category})."
    )
