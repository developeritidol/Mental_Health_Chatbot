from typing import Set

# In-memory storage
_blacklisted_tokens: Set[str] = set()


def add_to_blacklist(token: str) -> None:
    _blacklisted_tokens.add(token)


def is_blacklisted(token: str) -> bool:
    return token in _blacklisted_tokens


def cleanup_expired_blacklist() -> None:
    _blacklisted_tokens.clear()


def get_blacklist_size() -> int:
    return len(_blacklisted_tokens)