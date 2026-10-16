from __future__ import annotations

import pytest

import scripts.telegram_mutation_staging as mutation_staging
from scripts.telegram_mutation_staging import MutationProbeError, parse_integer, validate_live_gate


def test_mutation_probe_requires_independent_live_signals():
    try:
        validate_live_gate(live=True, confirm_live=True, allow_mutation=False, env_value="1")
    except MutationProbeError as error:
        assert str(error) == "mutation_live_gate_required"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("mutation probe must fail closed")


def test_mutation_probe_integer_inputs_are_bounded():
    assert parse_integer("-100123", required=True, label="chat_id") == -100123
    assert parse_integer("", required=False, label="thread_id") is None
    for value in ("0", "abc", "1" * 21):
        try:
            parse_integer(value, required=True, label="chat_id")
        except MutationProbeError:
            continue
        raise AssertionError(f"invalid chat id accepted: {value}")


@pytest.mark.asyncio
async def test_mutation_probe_executes_fixed_contract_without_network(monkeypatch):
    class FakeMessage:
        message_id = 41

    class FakeBot:
        def __init__(self):
            self.calls: list[tuple[str, dict]] = []

        async def send_message(self, **kwargs):
            self.calls.append(("send_message", kwargs))
            return FakeMessage()

        async def edit_message_text(self, **kwargs):
            self.calls.append(("edit_message_text", kwargs))

        async def send_video(self, **kwargs):
            self.calls.append(("send_video", kwargs))

        async def get_file(self, file_id):
            self.calls.append(("get_file", {"file_id": file_id}))

        async def send_document(self, **kwargs):
            self.calls.append(("send_document", kwargs))

        async def send_sticker(self, **kwargs):
            self.calls.append(("send_sticker", kwargs))

        async def answer_callback_query(self, callback_id, **kwargs):
            self.calls.append(("answer_callback_query", {"callback_id": callback_id, **kwargs}))

        async def shutdown(self):
            self.calls.append(("shutdown", {}))

    fake_bot = FakeBot()
    monkeypatch.setattr(mutation_staging, "build_bot", lambda token: fake_bot)

    report = await mutation_staging.run_live(
        "123456:abcdefghijklmnopqrstuvwxyz",
        chat_id=-100123,
        thread_id=17,
        video_file_id="video-file",
        sticker_file_id="sticker-file",
        file_id="document-file",
        callback_query_id="callback-id",
    )

    assert report["ok"] is True
    assert report["passed"] == 8
    assert report["failed"] == 0
    assert report["errors"] == {}
    assert [name for name, _ in fake_bot.calls] == [
        "send_message",
        "edit_message_text",
        "send_message",
        "send_video",
        "get_file",
        "send_document",
        "send_sticker",
        "answer_callback_query",
        "shutdown",
    ]


@pytest.mark.asyncio
async def test_mutation_probe_omits_forum_operation_for_normal_supergroup(monkeypatch):
    class FakeMessage:
        message_id = 41

    class FakeBot:
        def __init__(self):
            self.calls: list[tuple[str, dict]] = []

        async def send_message(self, **kwargs):
            self.calls.append(("send_message", kwargs))
            return FakeMessage()

        async def edit_message_text(self, **kwargs):
            self.calls.append(("edit_message_text", kwargs))

        async def send_video(self, **kwargs):
            self.calls.append(("send_video", kwargs))

        async def get_file(self, file_id):
            self.calls.append(("get_file", {"file_id": file_id}))

        async def send_document(self, **kwargs):
            self.calls.append(("send_document", kwargs))

        async def send_sticker(self, **kwargs):
            self.calls.append(("send_sticker", kwargs))

        async def answer_callback_query(self, callback_id, **kwargs):
            self.calls.append(("answer_callback_query", {"callback_id": callback_id, **kwargs}))

        async def shutdown(self):
            self.calls.append(("shutdown", {}))

    fake_bot = FakeBot()
    monkeypatch.setattr(mutation_staging, "build_bot", lambda token: fake_bot)

    report = await mutation_staging.run_live(
        "123456:abcdefghijklmnopqrstuvwxyz",
        chat_id=-1003896362491,
        thread_id=None,
        video_file_id="video-file",
        sticker_file_id="sticker-file",
        file_id="document-file",
        callback_query_id="callback-id",
    )

    assert report["ok"] is True
    assert report["passed"] == 7
    assert report["failed"] == 0
    assert "sendMessageForum" not in report["operations"]
    assert [name for name, _ in fake_bot.calls] == [
        "send_message",
        "edit_message_text",
        "send_video",
        "get_file",
        "send_document",
        "send_sticker",
        "answer_callback_query",
        "shutdown",
    ]

