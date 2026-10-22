"""Black-box regressions for shared puzzle routing; production files are peer owned."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.handlers.puzzle_suite import PuzzleSuite
from src.services.grid_puzzles_service import GridPuzzlesService
from src.handlers.word_puzzles_handler import WordPuzzlesHandler
from src.services.word_puzzles_service import WordPuzzlesService


class Repository:
    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat_id, key):
        from copy import deepcopy
        return deepcopy(self.rows.get((chat_id, key)))

    async def create_game_state_if_absent(self, chat_id, key, state):
        from copy import deepcopy
        if (chat_id, key) in self.rows:
            return False
        self.rows[(chat_id, key)] = deepcopy(state)
        return True

    async def compare_and_save_game_state(self, chat_id, key, state, *, expected_version):
        from copy import deepcopy
        prior = self.rows.get((chat_id, key))
        if prior is None or prior["version"] != expected_version:
            return False
        self.rows[(chat_id, key)] = deepcopy(state)
        return True


@pytest.mark.asyncio
async def test_arcade_start_callback_is_answered():
    adapter = SimpleNamespace(command=AsyncMock())
    query = SimpleNamespace(data="game_start_sudoku", answer=AsyncMock())
    update = SimpleNamespace(callback_query=query)
    suite = PuzzleSuite({"sudoku": adapter})
    assert await suite.callback(update, SimpleNamespace()) is True
    adapter.command.assert_awaited_once()
    query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_grid_late_panel_ack_schedules_repair_after_newer_ack():
    service = GridPuzzlesService(Repository())
    _, state = await service.open(game_key="hafiza_eslestirme", chat_id=-100,
                                  user_id=1, user_name="Bir")
    first = await service.claim_delivery(game_key="hafiza_eslestirme", chat_id=-100,
                                          now=100)
    assert first
    second = await service.claim_delivery(game_key="hafiza_eslestirme", chat_id=-100,
                                           now=131)
    assert second
    assert await service.acknowledge_panel(game_key="hafiza_eslestirme", chat_id=-100,
        round_id=state["round_id"], expected_version=second["version"],
        message_id=40, now=132)
    current = await service.current(game_key="hafiza_eslestirme", chat_id=-100)
    assert not current["panel_dirty"]
    assert not await service.acknowledge_panel(game_key="hafiza_eslestirme", chat_id=-100,
        round_id=state["round_id"], expected_version=first["version"],
        message_id=40, now=133)
    repaired = await service.current(game_key="hafiza_eslestirme", chat_id=-100)
    assert repaired["panel_dirty"] is True


@pytest.mark.asyncio
async def test_word_explicit_refresh_edits_existing_clean_panel():
    service = WordPuzzlesService(Repository())
    handler = WordPuzzlesHandler(service)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=40)),
                          edit_message_text=AsyncMock())
    await service.open(game_key="wordle_tr", chat_id=-100,
                       user_id=1, user_name="Bir")
    assert await handler.publish(bot, game_key="wordle_tr", chat_id=-100)
    current = await service.current(game_key="wordle_tr", chat_id=-100)
    assert current["panel_message_id"] == 40 and not current["panel_dirty"]
    assert await handler.publish(bot, game_key="wordle_tr", chat_id=-100, force=True)
    bot.edit_message_text.assert_awaited_once()
