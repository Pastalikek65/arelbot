"""Arel Tahmin game state and bounded normalization helpers.

The upstream Akinator client owns the network protocol.  This module owns
only the Telegram-safe, serializable state that survives a process restart.
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping


SCHEMA_VERSION = 1
ANSWER_CHOICES = ("0", "1", "2", "3", "4")


def normalize_akinator_theme(value: str | None) -> str:
    """Return the only Turkish theme currently supported by ``akipy``.

    ``akipy`` exposes Turkish character data under ``c``.  Unknown or future
    theme labels deliberately fall back to that safe default instead of
    sending an invalid upstream request.
    """

    aliases = {
        "c": "c",
        "karakter": "c",
        "character": "c",
        "kisi": "c",
        "kişi": "c",
    }
    return aliases.get(str(value or "").strip().casefold(), "c")


def new_akinator_state(
    *, owner_id: int, owner_name: str, chat_id: int, thread_id: int | None = None
) -> dict[str, Any]:
    """Create a fresh single-player round with an opaque callback token."""

    return {
        "schema_version": SCHEMA_VERSION,
        "game_key": "akinator",
        "round_id": uuid.uuid4().hex[:20],
        "chat_id": int(chat_id),
        "thread_id": int(thread_id) if thread_id is not None else None,
        "owner_id": int(owner_id),
        "owner_name": str(owner_name or "Oyuncu")[:80],
        "status": "pending",
        "language": "tr",
        "theme": "c",
        # The general character catalog can produce named proposals for
        # people that the upstream child catalog leaves unanswered.
        "child_mode": False,
        "questions": 0,
        "revision": 0,
        "question": "",
        "progress": 0.0,
        "step": 0,
        "proposition": None,
        "proposition_description": "",
        "image_url": None,
        "prompt_message_id": 0,
        "last_action": None,
        "error_code": None,
    }


def sync_akinator_state(state: dict[str, Any], client: Any) -> dict[str, Any]:
    """Copy bounded values from an ``akipy.Akinator`` instance into state."""

    state["question"] = str(getattr(client, "question", "") or "")[:1200]
    try:
        state["progress"] = max(0.0, min(100.0, float(getattr(client, "progression", 0) or 0)))
    except (TypeError, ValueError):
        state["progress"] = 0.0
    try:
        state["step"] = max(0, int(getattr(client, "step", 0) or 0))
    except (TypeError, ValueError):
        state["step"] = 0
    state["image_url"] = str(getattr(client, "akinator_image_url", "") or "")[:500] or None
    state["proposition"] = str(getattr(client, "name_proposition", "") or "")[:300] or None
    state["proposition_description"] = str(
        getattr(client, "description_proposition", "") or ""
    )[:1000]
    if bool(getattr(client, "finished", False)):
        state["status"] = "finished" if bool(getattr(client, "win", False)) and state["proposition"] else "failed"
        if state["status"] == "failed":
            state["proposition"] = None
            state["error_code"] = "AKINATOR_NO_GUESS"
    elif bool(getattr(client, "win", False)):
        state["status"] = "proposition" if state["proposition"] else "failed"
        if not state["proposition"]:
            state["error_code"] = "AKINATOR_NO_GUESS"
    else:
        state["status"] = "playing"
    return state


def validate_akinator_state(value: Mapping[str, Any]) -> bool:
    """Small persistence guard used before a state enters a callback path."""

    try:
        return (
            str(value.get("game_key")) == "akinator"
            and len(str(value.get("round_id") or "")) >= 12
            and int(value.get("chat_id")) != 0
            and int(value.get("owner_id")) > 0
            and str(value.get("status")) in {"pending", "playing", "proposition", "finished", "failed"}
        )
    except (TypeError, ValueError):
        return False
