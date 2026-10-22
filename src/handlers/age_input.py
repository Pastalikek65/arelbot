"""Bind a Telegram guess to the photo and forum topic actually being played."""

from datetime import datetime, timezone
import math

from src.storage.game_scope import forum_thread_id


def is_current_age_message(state, message):
    if not state or not message or state.get("status") != "playing":
        return False
    if state.get("delivery_pending") or state.get("initial_delivery_pending"):
        return False
    if state.get("thread_id") != forum_thread_id(message):
        return False
    prompt_id = int(state.get("prompt_message_id") or 0)
    if not prompt_id or int(getattr(message, "message_id", 0) or 0) <= prompt_id:
        return False
    reply = getattr(message, "reply_to_message", None)
    if reply is not None and int(getattr(reply, "message_id", 0) or 0) != prompt_id:
        return False
    sent = getattr(message, "date", None)
    if sent is not None:
        try:
            started = datetime.fromisoformat(str(state["round_started_at"]))
            started = started if started.tzinfo else started.replace(tzinfo=timezone.utc)
            # Telegram timestamps have second precision; the monotonic chat
            # message id above separates same-second photos and guesses.
            if sent.timestamp() < math.floor(started.timestamp()):
                return False
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            return False
    return True
