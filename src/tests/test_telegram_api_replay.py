"""Replay the real python-telegram-bot client against a deterministic API."""

from __future__ import annotations

import io

import httpx
import pytest
from telegram import Bot
from telegram.error import BadRequest
from telegram.request import HTTPXRequest


class _TelegramApiReplay:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.calls: list[tuple[str, str, bytes]] = []
        self.fail_send = fail_send

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.content))
        if request.url.path.endswith("/getMe"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "id": 123456789,
                        "is_bot": True,
                        "first_name": "Arel",
                        "username": "arel_replay_bot",
                    },
                },
                request=request,
            )
        if request.url.path.endswith("/sendMessage"):
            if self.fail_send:
                return httpx.Response(
                    400,
                    json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "message_id": 7,
                        "date": 1,
                        "chat": {"id": -1001, "type": "supergroup"},
                        "text": "fixture response",
                    },
                },
                request=request,
            )
        if request.url.path.endswith("/editMessageText"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "message_id": 7,
                        "date": 1,
                        "chat": {"id": -1001, "type": "supergroup"},
                        "text": "edited fixture response",
                    },
                },
                request=request,
            )
        if request.url.path.endswith("/sendVideo"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "message_id": 8,
                        "date": 1,
                        "chat": {"id": -1001, "type": "supergroup"},
                        "video": {
                            "file_id": "telegram-video-fixture",
                            "file_unique_id": "telegram-video-unique",
                            "width": 2,
                            "height": 2,
                            "duration": 1,
                        },
                    },
                },
                request=request,
            )
        if request.url.path.endswith("/sendDocument"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "message_id": 9,
                        "date": 1,
                        "chat": {"id": -1001, "type": "supergroup"},
                        "document": {
                            "file_id": "telegram-document-fixture",
                            "file_unique_id": "telegram-document-unique",
                            "file_name": "fixture.pdf",
                            "mime_type": "application/pdf",
                            "file_size": 8,
                        },
                    },
                },
                request=request,
            )
        if request.url.path.endswith("/sendSticker"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "message_id": 10,
                        "date": 1,
                        "chat": {"id": -1001, "type": "supergroup"},
                        "sticker": {
                            "file_id": "telegram-sticker-fixture",
                            "file_unique_id": "telegram-sticker-unique",
                            "type": "regular",
                            "width": 2,
                            "height": 2,
                            "is_animated": False,
                            "is_video": False,
                            "emoji": "😀",
                        },
                    },
                },
                request=request,
            )
        if request.url.path.endswith("/answerCallbackQuery"):
            return httpx.Response(200, json={"ok": True, "result": True}, request=request)
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {"file_id": "fixture-file", "file_unique_id": "fixture-unique", "file_path": "documents/fixture.bin"},
                },
                request=request,
            )
        return httpx.Response(
            404,
            json={"ok": False, "error_code": 404, "description": "fixture route missing"},
            request=request,
        )


def _bot(replay: _TelegramApiReplay) -> Bot:
    transport = httpx.MockTransport(replay)
    request = HTTPXRequest(httpx_kwargs={"transport": transport})
    get_updates_request = HTTPXRequest(httpx_kwargs={"transport": transport})
    return Bot(
        "123456789:fixture-token-never-logged",
        request=request,
        get_updates_request=get_updates_request,
    )


@pytest.mark.asyncio
async def test_real_ptb_client_replay_get_me_and_send_message():
    replay = _TelegramApiReplay()
    bot = _bot(replay)
    try:
        identity = await bot.get_me()
        message = await bot.send_message(chat_id=-1001, text="fixture hello", parse_mode="HTML")
    finally:
        await bot.shutdown()

    assert identity.username == "arel_replay_bot"
    assert message.message_id == 7
    assert [path for _, path, _ in replay.calls] == [
        "/bot123456789:fixture-token-never-logged/getMe",
        "/bot123456789:fixture-token-never-logged/sendMessage",
    ]
    assert b"chat_id=-1001" in replay.calls[1][2]
    assert b"text=fixture+hello" in replay.calls[1][2]


@pytest.mark.asyncio
async def test_real_ptb_client_replay_surfaces_safe_api_error():
    replay = _TelegramApiReplay(fail_send=True)
    bot = _bot(replay)
    try:
        with pytest.raises(BadRequest, match="(?i)chat not found") as raised:
            await bot.send_message(chat_id=-1001, text="fixture hello")
    finally:
        await bot.shutdown()

    assert "fixture-token" not in str(raised.value)


@pytest.mark.asyncio
async def test_real_ptb_client_replay_covers_mutating_media_and_edit_calls():
    replay = _TelegramApiReplay()
    bot = _bot(replay)
    try:
        edited = await bot.edit_message_text(chat_id=-1001, message_id=7, text="edited fixture")
        sent = await bot.send_video(
            chat_id=-1001,
            video=io.BytesIO(b"fixture-video-bytes"),
            filename="fixture.mp4",
            caption="fixture caption",
        )
        fetched = await bot.get_file("fixture-file")
    finally:
        await bot.shutdown()

    assert edited.message_id == 7
    assert sent.video.file_id == "telegram-video-fixture"
    assert fetched.file_path.endswith("/documents/fixture.bin")
    paths = [path for _, path, _ in replay.calls]
    assert paths[-3:] == ["/bot123456789:fixture-token-never-logged/editMessageText", "/bot123456789:fixture-token-never-logged/sendVideo", "/bot123456789:fixture-token-never-logged/getFile"]
    assert b"fixture.mp4" in replay.calls[-2][2]


@pytest.mark.asyncio
async def test_real_ptb_client_replay_covers_forum_callback_sticker_and_document_surfaces():
    """Exercise the Telegram surfaces used by group UI and asset workers."""

    replay = _TelegramApiReplay()
    bot = _bot(replay)
    try:
        forum_message = await bot.send_message(
            chat_id=-1001,
            message_thread_id=42,
            text="forum fixture",
        )
        callback_ok = await bot.answer_callback_query(
            callback_query_id="callback-fixture",
            text="Tamam",
            show_alert=False,
        )
        document = await bot.send_document(
            chat_id=-1001,
            document=io.BytesIO(b"fixture-pdf"),
            filename="fixture.pdf",
        )
        sticker = await bot.send_sticker(
            chat_id=-1001,
            sticker=io.BytesIO(b"fixture-sticker"),
            emoji="😀",
        )
    finally:
        await bot.shutdown()

    assert forum_message.message_id == 7
    assert callback_ok is True
    assert document.document.file_id == "telegram-document-fixture"
    assert sticker.sticker.file_id == "telegram-sticker-fixture"
    paths = [path for _, path, _ in replay.calls]
    assert paths[-4:] == [
        "/bot123456789:fixture-token-never-logged/sendMessage",
        "/bot123456789:fixture-token-never-logged/answerCallbackQuery",
        "/bot123456789:fixture-token-never-logged/sendDocument",
        "/bot123456789:fixture-token-never-logged/sendSticker",
    ]
    assert b"message_thread_id=42" in replay.calls[-4][2]
    assert b"fixture.pdf" in replay.calls[-2][2]
    assert b"fixture-sticker" in replay.calls[-1][2]
