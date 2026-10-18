"""Deterministic helpers for assembling AI conversation history."""

from __future__ import annotations

from typing import Any
import re


_MAX_USER_LABEL_CHARS = 80


def safe_user_label(value: Any) -> str:
    """Return a bounded label that cannot break the persisted turn envelope."""

    raw = str(value or "")
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", raw)
    cleaned = cleaned.replace("[", "(").replace("]", ")").replace(":", " - ")
    cleaned = " ".join(cleaned.split())[:_MAX_USER_LABEL_CHARS].strip()
    return cleaned or "Kullan\u0131c\u0131"


def format_user_turn(display_name: Any, message: Any) -> str:
    """Use one bounded, deterministic envelope for history and current turns."""

    return f"[{safe_user_label(display_name)}]: {str(message or '')}"


def format_user_history_turn(user_id: Any, content: Any) -> str:
    """Attach a stable Telegram identity to a persisted group turn.

    Display names can change and are not unique.  The numeric Telegram id is
    the identity boundary used by the model when several people share a chat.
    """

    try:
        stable_id = int(user_id)
    except (TypeError, ValueError):
        return str(content or "")
    return f"[telegram_user_id={stable_id}] {str(content or '')}"


def history_without_current_user_turn(
    history: list[dict[str, Any]],
    current_text: str | None,
    current_user_id: int | None = None,
) -> list[dict[str, Any]]:
    """Drop one persisted copy of the current user turn.

    Telegram handlers persist a message before invoking ChatEngine. The
    current request is appended separately as a fresh user message. In a group,
    the stable Telegram user id lets us remove the matching turn even when a
    different user sent an identical text afterwards. Without that identity,
    legacy private-chat behavior remains tail-only. Earlier identical
    questions remain valid context and assistant tails are never discarded.
    """
    if not history:
        return list(history)
    text = str(current_text or "").strip()
    if not text:
        return list(history)
    indexes = range(len(history) - 1, -1, -1) if current_user_id is not None else (len(history) - 1,)
    for index in indexes:
        item = history[index]
        if not isinstance(item, dict) or str(item.get("role") or "").casefold() != "user":
            continue
        content = str(item.get("content") or "").strip()
        identity_match = re.match(r"^\[telegram_user_id=(\d+)\]\s*", content)
        if current_user_id is not None:
            if identity_match is None or int(identity_match.group(1)) != int(current_user_id):
                continue
        content = re.sub(r"^\[telegram_user_id=[^\]]+\]\s*", "", content)
        match = re.match(r"^\[[^\]]*\]:\s*(.*)$", content, flags=re.DOTALL)
        candidate = match.group(1).strip() if match else content
        command_prefixes = ("/ara ", "/search ")
        for prefix in command_prefixes:
            if candidate.casefold().startswith(prefix):
                candidate = candidate[len(prefix):].strip()
                break
        if candidate == text:
            return list(history[:index] + history[index + 1:])
    return list(history)
