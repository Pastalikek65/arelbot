from types import SimpleNamespace

import pytest

from src.handlers.message_handler import MessageHandler


@pytest.mark.asyncio
async def test_text_game_result_uses_typed_progression_and_economy_event_ids():
    class Leaderboard:
        def __init__(self):
            self.calls = []

        async def record_score(self, **kwargs):
            self.calls.append(kwargs)
            return True

    class Economy:
        def __init__(self):
            self.calls = []

        async def credit(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return True

    leaderboard = Leaderboard()
    economy = Economy()
    handler = MessageHandler(
        SimpleNamespace(),
        SimpleNamespace(),
        None,
        SimpleNamespace(),
        leaderboard_service=leaderboard,
        economy_service=economy,
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7, full_name="Arel"),
        effective_chat=SimpleNamespace(id=-100),
        effective_message=SimpleNamespace(message_id=55),
    )

    await handler._record_game_result(update, "hangman", 10, "win")

    assert leaderboard.calls[0]["event_id"] == "hangman:-100:55:7:win"
    assert leaderboard.calls[0]["score"] == 10
    assert economy.calls[0][1]["event_id"] == "coin:hangman:-100:55:7:win"
    assert economy.calls[0][1]["amount"] == 5


@pytest.mark.asyncio
async def test_text_game_result_does_not_reward_replayed_score():
    class Leaderboard:
        async def record_score(self, **kwargs):
            return False

    class Economy:
        def __init__(self):
            self.called = False

        async def credit(self, *args, **kwargs):
            self.called = True

    economy = Economy()
    handler = MessageHandler(
        SimpleNamespace(), SimpleNamespace(), None, SimpleNamespace(),
        leaderboard_service=Leaderboard(), economy_service=economy,
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1, first_name="Oyuncu"),
        effective_chat=SimpleNamespace(id=-5),
        effective_message=SimpleNamespace(message_id=8),
    )

    await handler._record_game_result(update, "word_game", 8, "win")
    assert economy.called is False

