"""
Connection Registry
────────────────────
In-process set of counselor IDs that currently have an active WebSocket
connection on THIS server process.

Used by routing_service._is_available() as a real-time gate: a counselor
whose is_online flag is True in MongoDB but whose network dropped in the
last 45 seconds still passes the heartbeat check, but NOT this registry
check — preventing ghost assignments.

human.py calls mark_counselor_connected() on join and
mark_counselor_disconnected() on disconnect/finally.
routing_service.py calls is_counselor_connected() before confirming availability.
"""

_connected: set[str] = set()


def mark_counselor_connected(counselor_id: str) -> None:
    _connected.add(counselor_id)


def mark_counselor_disconnected(counselor_id: str) -> None:
    _connected.discard(counselor_id)


def is_counselor_connected(counselor_id: str) -> bool:
    return counselor_id in _connected
