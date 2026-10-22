"""Menu stop keeps legacy-board terminal panels and winner obligations durable."""

from types import SimpleNamespace

import pytest
from telegram.error import TimedOut

from src.games.chess import new_chess_state
from src.handlers.callback_handler import CallbackHandler
from src.services.board_panel_service import BoardPanelService
from src.services.word_game_ui import new_connect4_state
from src.tests.test_board_panel_service import Bot, Ledger, Repository


class Port(Repository):
    def __init__(self):
        super().__init__()
        self.deleted = []

    async def delete_game_state(self, chat_id, game_type):
        self.deleted.append((chat_id, game_type))
        self.rows.pop((chat_id, game_type), None)


class Query:
    def __init__(self):
        self.id = "menu-stop-1"
        self.data = "game_stop_all"
        self.message = SimpleNamespace(message_id=900, message_thread_id=None)
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


@pytest.mark.asyncio
async def test_menu_stop_keeps_cancel_panel_and_existing_winner_reward_pending():
    repo, ledger, bot = Port(), Ledger(), Bot()
    service = BoardPanelService(repo, leaderboard=ledger, economy=ledger)
    playing = new_connect4_state(owner_id=1, owner_name="Bir", chat_id=-100)
    playing["players"].append({"id": 2, "name": "Iki", "token": "🟡"})
    playing.update(status="playing", current_player_id=1)
    await service.save(-100, "connect4", playing)
    assert await service.publish(bot, -100, "connect4")
    canonical = (await service.current(-100, "connect4"))["prompt_message_id"]

    chess = new_chess_state(owner_id=1, owner_name="Bir", chat_id=-100)
    chess["players"].append({"id": 2, "name": "Iki", "color": "black"})
    chess["players"][0]["color"] = "white"
    chess.update(status="finished", winner_id=1, termination="checkmate", result="1-0")
    pending = await service.save(-100, "chess", chess)
    assert pending["board_score_pending"]

    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=1), None,
                              data_port=repo, leaderboard_service=ledger,
                              economy_service=ledger)
    handler.board_panels = service
    query = Query()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100, type="supergroup"),
                             effective_user=SimpleNamespace(id=1, first_name="Bir"),
                             callback_query=query)
    context = SimpleNamespace(chat_data={}, bot=bot)
    await handler.handle_callback_query(update, context)

    cancelled = await service.current(-100, "connect4")
    still_pending = await service.current(-100, "chess")
    assert cancelled["status"] == "cancelled"
    assert cancelled["prompt_message_id"] == canonical
    assert not cancelled["board_panel_dirty"]
    assert any(edit["message_id"] == canonical for edit in bot.edits)
    assert still_pending["board_score_pending"] and still_pending["winner_id"] == 1
    assert not any(key in {"connect4", "chess"} for _, key in repo.deleted)
    assert query.answers and "sonlandırıldı" in query.answers[-1][0][0]

    await service.process(bot, -100, "chess")
    assert len(ledger.scores) == 1 and len(ledger.coins) == 1
    assert not (await service.current(-100, "chess"))["board_score_pending"]


@pytest.mark.asyncio
async def test_menu_stop_panel_failure_remains_due_for_retry():
    repo, bot = Port(), Bot()
    service = BoardPanelService(repo)
    opened = await service.save(-100, "connect4",
        new_connect4_state(owner_id=1, owner_name="Bir", chat_id=-100))
    await service.publish(bot, -100, "connect4")
    state = await service.current(-100, "connect4")
    state.update(status="playing")
    await service.save(-100, "connect4", state)
    await service.publish(bot, -100, "connect4")
    bot.edit_error = TimedOut()

    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=1), None,
                              data_port=repo)
    handler.board_panels = service
    query = Query()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100, type="supergroup"),
                             effective_user=SimpleNamespace(id=1, first_name="Bir"),
                             callback_query=query)
    await handler.handle_callback_query(update, SimpleNamespace(chat_data={}, bot=bot))
    saved = await service.current(-100, "connect4")
    assert saved["status"] == "cancelled" and saved["board_panel_dirty"]
    assert saved["board_panel_retry_at"] > 0
    assert saved["round_id"] == opened["round_id"]
    bot.edit_error = None
    assert await service.publish(bot, -100, "connect4", force=True)
    assert not (await service.current(-100, "connect4"))["board_panel_dirty"]
