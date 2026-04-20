"""
Token Blacklist Module
----------------------
Simple in-memory token blacklist for logout functionality.
"""

from typing import Set

# In-memory storage
_blacklisted_tokens: Set[str] = set()


def add_to_blacklist(token: str) -> None:
    """Add a token to the blacklist."""
    _blacklisted_tokens.add(token)


def is_blacklisted(token: str) -> bool:
    """Check if token is blacklisted."""
    return token in _blacklisted_tokens


def cleanup_expired_blacklist() -> None:
    """Clear blacklist (basic cleanup)."""
    _blacklisted_tokens.clear()


def get_blacklist_size() -> int:
    """Return blacklist size."""
    return len(_blacklisted_tokens)