"""Real PTB serialization and HTTP replay, including safe upload recovery."""

import json
from io import BytesIO
from urllib.parse import parse_qs

import httpx
import pytest
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

from src.telegram_request import ThemedHTTPXRequest, build_request_pair
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputFile


def keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Katıl", callback_data="sa:opaque:j")]])


def bot_with_replay(responses):
    calls = []

    def replay(request):
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "is_bot": True, "first_name": "Fixture"}})
        content = request.read()
        calls.append(
            dict(parse_qs(content.decode())) if "multipart" not in request.headers.get("content-type", "") else content
        )
        status, payload = responses.pop(0)
        if payload == "success":
            payload = {
                "ok": True,
                "result": {"message_id": 7, "date": 1, "chat": {"id": -1, "type": "supergroup"}, "text": "Fixture"},
            }
        return httpx.Response(status, json=payload)

    request = ThemedHTTPXRequest(httpx_kwargs={"transport": httpx.MockTransport(replay)})
    return Bot("123:fixture", request=request), request, calls


@pytest.mark.asyncio
async def test_real_wire_uses_custom_icon_and_style_without_changing_protocol():
    bot, request, calls = bot_with_replay([(200, "success")])
    async with bot:
        await bot.send_message(-1, "Fixture", reply_markup=keyboard(), protect_content=True)
    button = json.loads(calls[0]["reply_markup"][0])["inline_keyboard"][0][0]
    assert button == {
        "text": "Katıl",
        "callback_data": "sa:opaque:j",
        "style": "success",
        "icon_custom_emoji_id": "5237699328843200968",
    }
    assert calls[0]["protect_content"] == ["true"]
    assert request.emoji_fallbacks == 0


@pytest.mark.asyncio
async def test_only_explicit_emoji_rejection_retries_and_disables_later_attempts():
    bot, request, calls = bot_with_replay(
        [(400, {"ok": False, "description": "Bad Request: CUSTOM_EMOJI_INVALID"}), (200, "success"), (200, "success")]
    )
    async with bot:
        await bot.send_message(-1, "Fixture", reply_markup=keyboard())
        await bot.send_message(-1, "Next", reply_markup=keyboard())
    assert len(calls) == 3 and request.emoji_fallbacks == 1 and not request.premium_buttons
    for index in (1, 2):
        b = json.loads(calls[index]["reply_markup"][0])["inline_keyboard"][0][0]
        assert b["text"] == "✅ Katıl" and "icon_custom_emoji_id" not in b


@pytest.mark.parametrize(
    "status,payload,error",
    [
        (400, {"ok": False, "description": "Bad Request: chat not found"}, BadRequest),
        (429, {"ok": False, "description": "Too many requests", "parameters": {"retry_after": 1}}, RetryAfter),
        (500, {"ok": False, "description": "Internal Server Error"}, NetworkError),
    ],
)
@pytest.mark.asyncio
async def test_other_delivery_failures_never_duplicate_messages(status, payload, error):
    bot, request, calls = bot_with_replay([(status, payload)])
    async with bot:
        with pytest.raises(error):
            await bot.send_message(-1, "Fixture", reply_markup=keyboard())
    assert len(calls) == 1 and request.emoji_fallbacks == 0


@pytest.mark.asyncio
async def test_multipart_upload_survives_rejected_emoji_retry():
    bot, request, calls = bot_with_replay(
        [(400, {"ok": False, "description": "CUSTOM_EMOJI_INVALID"}), (200, "success")]
    )
    photo = BytesIO(b"fixture-photo-content")
    async with bot:
        await bot.send_photo(-1, InputFile(photo, read_file_handle=False), reply_markup=keyboard())
    assert len(calls) == 2
    assert all(b"fixture-photo-content" in body for body in calls)
    assert request.emoji_fallbacks == 1


@pytest.mark.asyncio
async def test_inline_edit_without_chat_id_never_uses_owner_only_custom_icons():
    bot, request, calls = bot_with_replay([(200, "success")])
    async with bot:
        await bot.edit_message_reply_markup(inline_message_id="opaque", reply_markup=keyboard())
    b = json.loads(calls[0]["reply_markup"][0])["inline_keyboard"][0][0]
    assert "icon_custom_emoji_id" not in b and b["text"] == "✅ Katıl"
    assert request.emoji_fallbacks == 0


def test_polling_request_is_separate_and_env_can_disable_custom_icons(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CUSTOM_BUTTON_EMOJI", "off")
    api, polling = build_request_pair()
    assert isinstance(api, ThemedHTTPXRequest) and not api.premium_buttons
    assert not isinstance(polling, ThemedHTTPXRequest)


@pytest.mark.asyncio
async def test_ambiguous_network_timeout_does_not_resend():
    calls = []

    def replay(request):
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "is_bot": True, "first_name": "Fixture"}})
        calls.append(request.url.path)
        raise httpx.ReadTimeout("fixture timeout")

    request = ThemedHTTPXRequest(httpx_kwargs={"transport": httpx.MockTransport(replay)})
    async with Bot("123:fixture", request=request) as bot:
        with pytest.raises(TimedOut):
            await bot.send_message(-1, "Fixture", reply_markup=keyboard())
    assert len(calls) == 1 and request.emoji_fallbacks == 0
