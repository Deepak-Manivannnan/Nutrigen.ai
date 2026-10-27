"""
Who the current request is for, and which accounts were just deleted.

- current_user_id: set by the chat and approve routes, so every agent trace
  row written during that run is tagged with its owner (agents themselves
  never need to know). asyncio tasks copy the context, so the parallel
  reviewers inherit it too.
- Deleted accounts: once an account is deleted, its still-unexpired 15-minute
  access tokens are refused at once, and a plan run that was already in
  progress for it saves nothing. Kept in memory: after a restart those tokens
  have long expired and the user row no longer exists anyway.
"""
from contextvars import ContextVar
from typing import Optional

current_user_id: ContextVar[Optional[int]] = ContextVar("current_user_id", default=None)

_deleted_user_ids: set = set()
_MAX_TRACKED = 10_000


def mark_deleted(user_id: int) -> None:
    if len(_deleted_user_ids) >= _MAX_TRACKED:
        _deleted_user_ids.clear()
    _deleted_user_ids.add(int(user_id))


def is_deleted(user_id: Optional[int]) -> bool:
    return user_id is not None and int(user_id) in _deleted_user_ids


def clear_deleted(user_id: int) -> None:
    """A new account that happens to get a freed id is not 'deleted'."""
    _deleted_user_ids.discard(int(user_id))
