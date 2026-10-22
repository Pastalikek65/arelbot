"""Use Telegram forum topics, never ordinary reply-thread identifiers."""


def forum_thread_id(message):
    if not message or not getattr(message, "is_topic_message", False):
        return None
    value = getattr(message, "message_thread_id", None)
    return int(value) if value is not None else None
