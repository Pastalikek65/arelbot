"""Scope helpers for Telegram conversation state.

Conversation state is user-owned, but a user can interact with the bot in
several private chats, groups, and forum topics at the same time.  Keeping
only the user id as a key lets a callback from one chat accidentally consume a
wizard started in another chat.  These helpers keep the handler surface
backwards compatible with plain dictionaries while preferring the durable
``PersistentStateMap`` scoped API.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


def scope_state(value: dict[str, Any], *, user_id: int, chat_id: int) -> dict[str, Any]:
    """Return a copy of *value* bound to one Telegram user/chat pair."""

    scoped = dict(value)
    scoped["_scope"] = {"user_id": int(user_id), "chat_id": int(chat_id)}
    return scoped


def state_matches(
    value: object,
    *,
    user_id: int,
    chat_id: int,
    allow_legacy_private: bool = True,
) -> bool:
    """Check ownership without trusting state data supplied by a callback.

    Older in-memory fixtures and pre-scope Redis records have no ``_scope``.
    They remain usable only in a private chat, where the user id itself is a
    sufficient boundary.  Group/forum state without an explicit scope fails
    closed so it cannot cross chat boundaries.
    """

    if not isinstance(value, dict):
        return False
    scope = value.get("_scope")
    if not isinstance(scope, dict):
        return bool(allow_legacy_private and int(chat_id) > 0)
    try:
        return int(scope.get("user_id")) == int(user_id) and int(scope.get("chat_id")) == int(chat_id)
    except (TypeError, ValueError):
        return False


def get_scoped_state(store: Any, *, user_id: int, chat_id: int) -> dict[str, Any] | None:
    """Read scoped state from the durable map or a legacy dict fixture."""

    getter = getattr(store, "get_for_scope", None)
    if callable(getter):
        scoped = getter(user_id, chat_id)
        if scoped is not None:
            return scoped
        # A rolling upgrade may still have a legacy ``user:<id>`` record.
        # It is deliberately eligible only for a private chat.
        legacy_getter = getattr(store, "get", None)
        if callable(legacy_getter) and int(chat_id) > 0:
            legacy = legacy_getter(user_id)
            if state_matches(legacy, user_id=user_id, chat_id=chat_id):
                return legacy
        return None
    if not isinstance(store, MutableMapping):
        return None
    value = store.get(user_id)
    return value if state_matches(value, user_id=user_id, chat_id=chat_id) else None


def set_scoped_state(store: Any, *, user_id: int, chat_id: int, value: dict[str, Any]) -> None:
    """Persist state under a user/chat scope."""

    setter = getattr(store, "set_for_scope", None)
    if callable(setter):
        setter(user_id, chat_id, value)
        return
    store[user_id] = scope_state(value, user_id=user_id, chat_id=chat_id)


def pop_scoped_state(store: Any, *, user_id: int, chat_id: int) -> dict[str, Any] | None:
    """Delete state only when it belongs to the current chat."""

    popper = getattr(store, "pop_for_scope", None)
    if callable(popper):
        return popper(user_id, chat_id)
    value = get_scoped_state(store, user_id=user_id, chat_id=chat_id)
    if value is not None:
        store.pop(user_id, None)
    return value
