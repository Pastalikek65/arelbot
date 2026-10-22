"""Cross-family Telegram routing regressions for the puzzle rollout."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.handlers.message_handler import MessageHandler
from src.handlers.puzzle_suite import PuzzleSuite


@pytest.mark.asyncio
async def test_reply_to_wordle_panel_is_not_taken_by_simultaneous_tabu_round():
    # A player can be on Tabu's active team while a Wordle game is open in the
    # same topic. The reply identifies Wordle even if the text is also a Tabu
    # guess. Routing it to Tabu can award the wrong game or swallow the guess.
    state = {"phase": "playing", "players": [{"id": 7, "active": True}],
             "owner_id": 7, "panel_message_id": 40}
    adapter = SimpleNamespace(
        service=SimpleNamespace(current=AsyncMock(return_value=state)),
        on_text=AsyncMock(return_value=True),
    )
    suite = PuzzleSuite({"wordle_tr": adapter})
    tabu = SimpleNamespace(on_text=AsyncMock(return_value=True))
    router = SimpleNamespace(tabu_handler=tabu, puzzle_suite=suite)
    message = SimpleNamespace(message_id=41, text="kalem", is_topic_message=False,
                              reply_to_message=SimpleNamespace(message_id=40))
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100, type="supergroup"),
                             effective_user=SimpleNamespace(id=7), effective_message=message)

    assert await MessageHandler._check_game_inputs(router, update,
        SimpleNamespace(), "kalem") is True
    adapter.on_text.assert_awaited_once()
    tabu.on_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_plain_overlap_requires_explicit_panel_reply_without_scoring():
    state = {"phase": "playing", "players": [{"id": 7, "active": True}],
             "owner_id": 7, "panel_message_id": 40}
    adapter = SimpleNamespace(
        service=SimpleNamespace(current=AsyncMock(return_value=state)),
        on_text=AsyncMock(return_value=True),
    )
    repository = SimpleNamespace(get_game_state=AsyncMock(
        side_effect=lambda _chat, key: {"phase": "active"} if key == "tabu" else None))
    suite = PuzzleSuite({"wordle_tr": adapter}, repository=repository)
    tabu = SimpleNamespace(on_text=AsyncMock(return_value=True))
    router = SimpleNamespace(tabu_handler=tabu, puzzle_suite=suite)
    message = SimpleNamespace(message_id=41, text="kalem", is_topic_message=False,
                              reply_to_message=None, reply_text=AsyncMock())
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100, type="supergroup"),
                             effective_user=SimpleNamespace(id=7), effective_message=message)

    assert await MessageHandler._check_game_inputs(router, update,
        SimpleNamespace(), "kalem") is True
    message.reply_text.assert_awaited_once()
    tabu.on_text.assert_not_awaited()
    adapter.on_text.assert_not_awaited()
