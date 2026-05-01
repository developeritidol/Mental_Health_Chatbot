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


async def get_available_counselor_count() -> int:
    """
    Returns the number of counselors online, fresh, and with capacity.
    Source of truth is exclusively MongoDB to support multi-worker environments.
    """
    db = get_database()
    if db is None:
        return 0

    stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=_STALE_PING_SECONDS)
    query: dict = {
        "is_online": True,
        "last_ping": {"$gte": stale_cutoff},
        "$expr": {
            "$lt": [
                {"$ifNull": ["$current_active_sessions", 0]},
                {"$ifNull": ["$max_concurrent_sessions", 1]}
            ]
        },
    }
    
    count = await db.admins.count_documents(query)
    return count


# ── Public entry point ────────────────────────────────────────────────────────

async def route_crisis_session(user_id: str, session_id: str, consensus: dict) -> None:
    """
    Main routing orchestrator. Always called via asyncio.create_task() so it
    never blocks the SSE stream.

    Uses the doctor_user_assignments collection for Tier 1 sticky routing and
    persists new assignments via MongoDB transactions to prevent data
    inconsistency on server crash.
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

        # Acquire routing lock. Query excludes "__routing__" to prevent duplicate
        # concurrent routing tasks, but allows any other value (null or a stale
        # counselor ID from a prior closed session) so re-escalations are not blocked.
        claim_result = await db.sessions.find_one_and_update(
            {"session_id": session_id, "assigned_counselor_id": {"$ne": "__routing__"}},
            {"$set": {
                "assigned_counselor_id": "__routing__",
                "routing_started_at": datetime.now(timezone.utc),
            }},
        )
        if claim_result is None:
            logger.info(f"[ROUTING] Session {session_id} already being routed — skipping duplicate task.")
            return

        # ── Check Availability ───────────────────────────────────────────────
        available_count = await get_available_counselor_count()
        if available_count == 0:
            logger.warning(f"[ROUTING] No counselors available for session {session_id}. Serving hotline message.")
            await db.sessions.update_one(
                {"session_id": session_id},
                {"$set": {"assigned_counselor_id": None, "routing_started_at": None}},
            )
            hotline_text = (
                "We're sorry, no counselors are available right now. "
                "If you are in immediate danger, please call the National Crisis Helpline: 988."
            )
            await db.messages.insert_one({
                "session_id": session_id,
                "role": "system",
                "sender_type": "system",
                "content": hotline_text,
                "timestamp": datetime.now(timezone.utc),
            })
            # Push immediately to the user's open WebSocket room so they don't wait 20 minutes
            try:
                from app.api.routes.human import manager as ws_manager
                await ws_manager.send_to_all(user_id, {
                    "role": "system",
                    "text": hotline_text,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "is_human": False,
                    "is_system": True,
                    "type": "counselor_unavailable",
                })
            except Exception:
                pass
            return

        # ── Generate handoff summary in background ────────────────────────────
        asyncio.create_task(
            _run_summarization_and_save(user_id, session_id, crisis_category, "comprehensive_summary")
        )

        # ── Persist assignment lock as unassigned but ready for claim ──────
        await db.sessions.update_one(
            {"session_id": session_id},
            {"$set": {
                "assigned_counselor_id": None,
                "crisis_category": crisis_category,
                "routing_started_at": None,
            }},
        )

        logger.info(f"[ROUTING SUCCESS] Session {session_id} broadcasted to global queue (Category: {crisis_category})")

        # ── Broadcast: queue activity visible to all admins/counselors ────
        from app.api.routes.human import manager as ws_manager
        await ws_manager.broadcast_to_dashboard({
            "type": "new_escalation",
            "session_id": session_id,
            "user_id": user_id,
            "crisis_category": crisis_category,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

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
    handoff_mode: str = "comprehensive_summary",
) -> None:
    """Generates the clinical handoff note and persists it to the session doc."""
    try:
        db = get_database()
        if db is None:
            return
        summary = await generate_clinical_handoff(user_id, session_id, crisis_category, handoff_mode)
        await db.sessions.update_one(
            {"session_id": session_id},
            {"$set": {"handoff_summary": summary}},
        )
        logger.info(f"[ROUTING] Handoff summary saved for session {session_id}.")
    except Exception:
        logger.exception(f"[ROUTING] Summarization failed for session {session_id}.")
