from types import SimpleNamespace

import pytest

from src.handlers.message_handler import MessageHandler


class _Message:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SimpleNamespace(message_id=999)


class _DataPort:
    reachable = False

    async def get_game_state(self, chat_id, game_type):
        return None

    async def save_game_state(self, chat_id, game_type, state):
        return None


class _JobQueue:
    def get_jobs_by_name(self, name):
        return []

    def run_once(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_wrong_word_guess_is_consumed_and_replies_to_game_panel():
    message = _Message()
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=42, type="supergroup"),
        effective_user=SimpleNamespace(id=7, mention_html=lambda: "<a>Arel</a>"),
        effective_message=message,
        message=message,
    )
    context = SimpleNamespace(
        chat_data={
            "word_guess": {
                "word": "KEDÄ°",
                "description": "Evlerde yaÅŸayan sevimli bir hayvan.",
                "hints": ["4 harflidir."],
                "hint_index": 0,
                "attempts": 0,
                "prompt_message_id": 314,
            }
        },
        job_queue=_JobQueue(),
    )
    handler = MessageHandler(
        SimpleNamespace(),
        SimpleNamespace(),
        None,
        SimpleNamespace(),
        data_port=_DataPort(),
    )

    consumed = await handler._check_game_inputs(update, context, "kÃ¶pek")

    assert consumed is True
    assert len(message.calls) == 1
    assert message.calls[0][1]["reply_to_message_id"] == 314
    assert context.chat_data["word_guess"]["attempts"] == 1
