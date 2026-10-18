"""Translate Telegram updates into bounded moderation domain events.

The normalizer is intentionally transport-only.  It does not make policy
decisions, call Telegram, or enqueue work.  Every update kind (text, caption,
GIF, photo, document, sticker, voice, video and audio) gets the same bounded
event shape so media messages cannot bypass group moderation accidentally.
"""

from __future__ import annotations

from typing import Any

from .contracts import ModerationEvent


_ATTACHMENT_FIELDS = (
    ("animation", "animation"),
    ("photo", "photo"),
    ("document", "document"),
    ("sticker", "sticker"),
    ("voice", "voice"),
    ("video", "video"),
    ("audio", "audio"),
    ("video_note", "video_note"),
)


def _bounded(value: Any, limit: int = 4096) -> str:
    if value is None:
        return ""
    return str(value)[:limit]


def _attachment(message: Any) -> tuple[str | None, str | None, str | None, str | None]:
    for field, kind in _ATTACHMENT_FIELDS:
        value = getattr(message, field, None)
        if value:
            mime = getattr(value, "mime_type", None)
            attachment_id = getattr(value, "file_unique_id", None) or getattr(value, "file_id", None)
            sticker_set_name = getattr(value, "set_name", None) if kind == "sticker" else None
            return kind, _bounded(mime, 128) or None, _bounded(attachment_id, 256) or None, _bounded(sticker_set_name, 128) or None
    return None, None, None, None


def build_moderation_event(update: Any, *, is_admin: bool = False) -> ModerationEvent | None:
    """Return a moderation event for a group update or ``None`` when absent.

    Bot-authored updates and private chats are deliberately ignored.  Missing
    message/user/chat identifiers are also ignored rather than guessed.
    """

    chat = getattr(update, "effective_chat", None)
    if getattr(chat, "type", None) not in {"group", "supergroup"}:
        return None
    user = getattr(update, "effective_user", None)
    if user is None or getattr(user, "is_bot", False):
        return None
    message = getattr(update, "effective_message", None) or getattr(update, "message", None)
    if message is None:
        return None
    message_id = getattr(message, "message_id", None)
    chat_id = getattr(chat, "id", None)
    user_id = getattr(user, "id", None)
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in (chat_id, message_id, user_id)):
        return None

    text = _bounded(getattr(message, "text", None))
    caption = _bounded(getattr(message, "caption", None)) or None
    attachment_type, attachment_mime, attachment_id, sticker_set_name = _attachment(message)
    event_kind = attachment_type or ("text" if text else "caption" if caption else "message")
    return ModerationEvent(
        chat_id=int(chat_id),
        message_id=int(message_id),
        user_id=int(user_id),
        event_kind=event_kind,
        text=text,
        caption=caption,
        attachment_type=attachment_type,
        attachment_mime=attachment_mime,
        attachment_id=attachment_id,
        sticker_set_name=sticker_set_name,
        is_edited=bool(getattr(update, "edited_message", None) is not None),
        thread_id=getattr(message, "message_thread_id", None),
        update_id=getattr(update, "update_id", None),
        is_admin=bool(is_admin),
    )


__all__ = ["build_moderation_event"]

