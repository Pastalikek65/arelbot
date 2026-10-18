import pytest

from src.chat_engine import ChatEngine


class _Database:
    async def get_history(self, _session_id, limit=15):
        return [
            {
                "role": "assistant",
                "content": "selam",
                "target_user_id": 12345,
                "reply_to_message_id": 67890,
            }
        ]


@pytest.mark.asyncio
async def test_chat_history_does_not_expose_telegram_reply_metadata_to_model():
    engine = object.__new__(ChatEngine)
    engine.db = _Database()

    history = await engine.get_chat_history(-100)

    assert history == [{"role": "assistant", "content": "selam"}]
    assert "user_id" not in history[0]["content"]
    assert "message_id" not in history[0]["content"]
