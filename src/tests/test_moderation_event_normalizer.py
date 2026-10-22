from __future__ import annotations

from types import SimpleNamespace

from src.moderation.event_normalizer import build_moderation_event


def _update(message):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
        effective_user=SimpleNamespace(id=42, is_bot=False),
        effective_message=message,
        message=message,
        update_id=77,
    )


def test_normalizer_accepts_text_message_and_keeps_telegram_identity():
    event = build_moderation_event(
        _update(SimpleNamespace(message_id=9, text="merhaba", caption=None, message_thread_id=4)),
        is_admin=True,
    )
    assert event is not None
    assert event.chat_id == -1001
    assert event.message_id == 9
    assert event.user_id == 42
    assert event.thread_id == 4
    assert event.text == "merhaba"
    assert event.event_kind == "text"
    assert event.is_admin is True


def test_normalizer_covers_gif_and_caption_without_starting_ai_work():
    message = SimpleNamespace(
        message_id=10,
        text=None,
        caption="https://example.test/video",
        message_thread_id=None,
        animation=SimpleNamespace(mime_type="image/gif"),
        document=None,
        photo=None,
        sticker=None,
        voice=None,
        video=None,
        audio=None,
    )
    event = build_moderation_event(_update(message))
    assert event is not None
    assert event.event_kind == "animation"
    assert event.attachment_type == "animation"
    assert event.attachment_mime == "image/gif"
    assert event.caption == "https://example.test/video"


def test_normalizer_ignores_bot_or_non_group_updates():
    update = _update(SimpleNamespace(message_id=1, text="x", caption=None))
    update.effective_chat.type = "private"
    assert build_moderation_event(update) is None
    update.effective_chat.type = "supergroup"
    update.effective_user.is_bot = True
    assert build_moderation_event(update) is None

