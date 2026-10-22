"""Board panel delivery, fencing and settlement regressions."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from bson import BSON
from telegram.error import BadRequest, RetryAfter, TimedOut

from src.games.chess import new_chess_state
from src.services.board_panel_service import BoardPanelService
from src.services.gomoku_ui import gomoku_move, gomoku_start, new_gomoku_state
from src.services.tictactoe_ui import new_tictactoe_state
from src.services.word_game_ui import new_connect4_state


class Repository:
    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat, key):
        return deepcopy(self.rows.get((chat, key)))

    async def create_game_state_if_absent(self, chat, key, state):
        if (chat, key) in self.rows:
            return False
        self.rows[chat, key] = deepcopy(state)
        return True

    async def initialize_game_state_version_if_missing(self, chat, key):
        state = self.rows.get((chat, key))
        if state is None or "version" in state:
            return False
        state["version"] = 0
        return True

    async def compare_and_save_game_state(self, chat, key, state, *, expected_version):
        previous = self.rows.get((chat, key))
        if not previous or previous["version"] != expected_version:
            return False
        assert state["version"] == expected_version + 1
        self.rows[chat, key] = deepcopy(state)
        return True


class Ledger:
    def __init__(self):
        self.scores = {}
        self.coins = {}
        self.fail_coin = False

    async def record_score(self, *, event_id, score, **kwargs):
        self.scores.setdefault(event_id, score)

    async def credit(self, uid, cid, amount, *, reason, event_id):
        if self.fail_coin:
            raise RuntimeError("coin_store_down")
        self.coins.setdefault(event_id, (uid, cid, amount, reason))


class Bot:
    def __init__(self):
        self.sent = []
        self.edits = []
        self.send_error = None
        self.edit_error = None

    async def send_message(self, **kwargs):
        if self.send_error:
            raise self.send_error
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def edit_message_text(self, **kwargs):
        if self.edit_error:
            raise self.edit_error
        self.edits.append(kwargs)


def fresh(key):
    factory = {
        "connect4": new_connect4_state,
        "tictactoe": new_tictactoe_state,
        "gomoku": new_gomoku_state,
        "chess": new_chess_state,
    }[key]
    return factory(owner_id=1, owner_name="Bir", chat_id=-100)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["connect4", "tictactoe", "gomoku", "chess"])
async def test_all_four_boards_save_and_deliver_single_canonical_panel(key):
    repo, bot = Repository(), Bot()
    service = BoardPanelService(repo)
    opened = await service.save(-100, key, fresh(key))
    assert opened["board_panel_revision"] == 1 and opened["board_panel_dirty"]
    assert await service.publish(bot, -100, key)
    live = await service.current(-100, key)
    assert live["prompt_message_id"] == 101 and not live["board_panel_dirty"]
    live["players"][0]["name"] = "Yeni"
    await service.save(-100, key, live)
    assert await service.publish(bot, -100, key)
    assert len(bot.sent) == 1 and len(bot.edits) == 1


@pytest.mark.asyncio
async def test_unknown_send_never_blindly_resends_and_force_recovers():
    repo, bot = Repository(), Bot()
    service = BoardPanelService(repo)
    await service.save(-100, "connect4", fresh("connect4"))
    bot.send_error = TimedOut()
    assert not await service.publish(bot, -100, "connect4")
    unknown = await service.current(-100, "connect4")
    assert unknown["board_panel_delivery_unknown"]
    assert not await service.publish(bot, -100, "connect4")
    bot.send_error = None
    assert await service.publish(bot, -100, "connect4", force=True)
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_deleted_panel_replaced_but_uncertain_edit_retried():
    repo, bot = Repository(), Bot()
    service = BoardPanelService(repo)
    await service.save(-100, "connect4", fresh("connect4"))
    assert await service.publish(bot, -100, "connect4")
    live = await service.current(-100, "connect4")
    await service.save(-100, "connect4", live)
    bot.edit_error = TimedOut()
    assert not await service.publish(bot, -100, "connect4")
    assert len(bot.sent) == 1
    bot.edit_error = BadRequest("Message to edit not found")
    assert await service.publish(bot, -100, "connect4", force=True)
    assert len(bot.sent) == 2


@pytest.mark.asyncio
async def test_stale_snapshot_and_round_adoption_rejected():
    repo = Repository()
    service = BoardPanelService(repo)
    first = await service.save(-100, "tictactoe", fresh("tictactoe"))
    second = await service.save(-100, "tictactoe", first)
    with pytest.raises(RuntimeError, match="board_state_conflict"):
        await service.save(-100, "tictactoe", first)
    assert not await service.adopt_callback(-100, "tictactoe", 55, "old-round")
    assert await service.adopt_callback(-100, "tictactoe", 55, second["round_id"])
    assert not await service.adopt_callback(-100, "tictactoe", 56, second["round_id"])


@pytest.mark.asyncio
async def test_turn_clock_starts_after_delivery_and_timeout_has_no_reward(monkeypatch):
    monkeypatch.setattr("src.services.board_panel_service.time.time", lambda: 100.0)
    repo, bot = Repository(), Bot()
    service = BoardPanelService(repo)
    opened = await service.save(-100, "tictactoe", fresh("tictactoe"))
    opened["players"].append({"id": 2, "name": "Iki", "token": "O"})
    opened.update(status="playing", current_player_id=1, turn_expires_at=110)
    ready = await service.save(-100, "tictactoe", opened)
    assert ready["turn_expires_at"] is None and ready["board_turn_waiting_delivery"]
    assert not await service.expire(-100, "tictactoe", now=1000)
    assert await service.publish(bot, -100, "tictactoe")
    live = await service.current(-100, "tictactoe")
    assert live["turn_expires_at"] == 100 + live["board_turn_duration"]
    assert await service.expire(-100, "tictactoe", now=1000)
    assert (await service.current(-100, "tictactoe"))["board_score_pending"] is not True


@pytest.mark.asyncio
async def test_terminal_score_and_coin_retry_are_idempotent_after_restart():
    repo, ledger, bot = Repository(), Ledger(), Bot()
    service = BoardPanelService(repo, leaderboard=ledger, economy=ledger)
    opened = await service.save(-100, "connect4", fresh("connect4"))
    opened["players"].append({"id": 2, "name": "Iki", "token": "🟡"})
    opened.update(status="won", winner_id=1)
    won = await service.save(-100, "connect4", opened)
    assert won["board_score_pending"]
    ledger.fail_coin = True
    with pytest.raises(RuntimeError, match="coin_store_down"):
        await service.process(bot, -100, "connect4")
    assert len(ledger.scores) == 1 and len(ledger.coins) == 0
    assert (await service.current(-100, "connect4"))["board_score_pending"]
    ledger.fail_coin = False
    restarted = BoardPanelService(repo, leaderboard=ledger, economy=ledger)
    assert await restarted.process(bot, -100, "connect4")
    assert len(ledger.scores) == 1 and len(ledger.coins) == 1
    assert not (await restarted.current(-100, "connect4"))["board_score_pending"]
    assert not await restarted.process(bot, -100, "connect4")


@pytest.mark.asyncio
async def test_cancel_requires_owner_and_preserves_terminal_panel():
    repo = Repository()
    service = BoardPanelService(repo)
    opened = await service.save(-100, "chess", fresh("chess"))
    assert not await service.cancel(-100, "chess", 2)
    assert await service.cancel(-100, "chess", 1)
    ended = await service.current(-100, "chess")
    assert ended["status"] == "cancelled" and ended["board_panel_dirty"]
    assert ended["round_id"] == opened["round_id"]


@pytest.mark.asyncio
async def test_reentrant_owner_guard_does_not_leak_to_child_task():
    repo = Repository()
    service = BoardPanelService(repo)
    async with service.guard(-100, "connect4"):
        opened = await service.save(-100, "connect4", fresh("connect4"))
        assert opened["version"] == 0
        child = asyncio.create_task(service.save(-100, "connect4", opened))
        with pytest.raises(ValueError, match="game_busy"):
            await child


@pytest.mark.asyncio
async def test_missing_required_reward_backend_keeps_obligation():
    repo, ledger, bot = Repository(), Ledger(), Bot()
    service = BoardPanelService(repo, leaderboard=ledger, economy=ledger)
    opened = await service.save(-100, "gomoku", fresh("gomoku"))
    opened["players"].append({"id": 2, "name": "Iki", "token": "⚪"})
    opened.update(status="won", winner_id=1)
    await service.save(-100, "gomoku", opened)
    without_coins = BoardPanelService(repo, leaderboard=ledger)
    with pytest.raises(RuntimeError, match="board_score_store_unavailable"):
        await without_coins.process(bot, -100, "gomoku")
    assert (await without_coins.current(-100, "gomoku"))["board_score_pending"]
    assert not ledger.scores and not ledger.coins


@pytest.mark.asyncio
async def test_new_round_cannot_erase_pending_winner_reward():
    repo, ledger = Repository(), Ledger()
    service = BoardPanelService(repo, leaderboard=ledger, economy=ledger)
    opened = await service.save(-100, "tictactoe", fresh("tictactoe"))
    opened["players"].append({"id": 2, "name": "Iki", "token": "O"})
    opened.update(status="won", winner_id=1)
    terminal = await service.save(-100, "tictactoe", opened)
    terminal["round_id"] = "new-round"
    terminal.update(status="playing", winner_id=None)
    with pytest.raises(RuntimeError, match="board_score_pending"):
        await service.save(-100, "tictactoe", terminal)


@pytest.mark.asyncio
async def test_explicitly_unfunded_board_stays_unfunded_after_restart():
    repo, ledger, bot = Repository(), Ledger(), Bot()
    without_rewards = BoardPanelService(repo)
    opened = await without_rewards.save(-100, "connect4", fresh("connect4"))
    assert opened["leaderboard_required"] is False
    assert opened["economy_required"] is False
    opened["players"].append({"id": 2, "name": "Iki", "token": "🟡"})
    opened.update(status="won", winner_id=1)
    await without_rewards.save(-100, "connect4", opened)
    restarted = BoardPanelService(repo, leaderboard=ledger, economy=ledger)
    await restarted.process(bot, -100, "connect4")
    assert ledger.scores == {} and ledger.coins == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("key,points", [
    ("connect4", 50), ("tictactoe", 30), ("gomoku", 100), ("chess", 100),
])
async def test_legacy_winner_score_and_coin_amounts_are_preserved(key, points):
    repo, ledger, bot = Repository(), Ledger(), Bot()
    service = BoardPanelService(repo, leaderboard=ledger, economy=ledger)
    opened = await service.save(-100, key, fresh(key))
    opened["players"].append({"id": 2, "name": "Iki"})
    opened.update(status="finished" if key == "chess" else "won", winner_id=1)
    await service.save(-100, key, opened)
    await service.process(bot, -100, key)
    assert list(ledger.scores.values()) == [points]
    assert next(iter(ledger.coins.values()))[2] == points // 2


@pytest.mark.asyncio
async def test_gomoku_new_turn_waits_for_next_delivered_panel(monkeypatch):
    monkeypatch.setattr("src.services.board_panel_service.time.time", lambda: 100.0)
    repo, bot = Repository(), Bot()
    service = BoardPanelService(repo)
    opened = await service.save(-100, "gomoku", fresh("gomoku"))
    opened["players"].append({"id": 2, "name": "Iki", "token": "⚪"})
    assert gomoku_start(opened)["changed"]
    waiting = await service.save(-100, "gomoku", opened)
    assert waiting["turn_expires_at"] is None
    assert await service.publish(bot, -100, "gomoku")
    active = await service.current(-100, "gomoku")
    assert active["turn_expires_at"] is not None
    assert gomoku_move(active, user_id=1, cell=0, action_id="first")["changed"]
    next_turn = await service.save(-100, "gomoku", active)
    assert next_turn["board_turn_waiting_delivery"]
    assert next_turn["turn_expires_at"] is None
    assert await service.publish(bot, -100, "gomoku")
    after = await service.current(-100, "gomoku")
    assert after["turn_expires_at"] == 100 + after["board_turn_duration"]


@pytest.mark.asyncio
async def test_force_refreshes_clean_canonical_panel_without_new_message():
    repo, bot = Repository(), Bot()
    service = BoardPanelService(repo)
    await service.save(-100, "connect4", fresh("connect4"))
    assert await service.publish(bot, -100, "connect4")
    assert await service.process(bot, -100, "connect4", force=True)
    assert len(bot.sent) == 1 and len(bot.edits) == 1


@pytest.mark.asyncio
async def test_new_round_uses_new_reward_sink_requirements():
    repo, ledger = Repository(), Ledger()
    old_service = BoardPanelService(repo)
    first = await old_service.save(-100, "connect4", fresh("connect4"))
    first.update(status="cancelled")
    ended = await old_service.save(-100, "connect4", first)
    next_round = fresh("connect4")
    next_round["version"] = ended["version"]
    restarted = BoardPanelService(repo, leaderboard=ledger, economy=ledger)
    saved = await restarted.save(-100, "connect4", next_round)
    assert saved["leaderboard_required"] is True
    assert saved["economy_required"] is True
    assert saved["board_score_pending"] is False


@pytest.mark.asyncio
async def test_not_modified_edit_is_delivery_ack_and_bson_safe():
    repo, bot = Repository(), Bot()
    first = BoardPanelService(repo)
    opened = await first.save(-100, "chess", fresh("chess"))
    assert BSON(BSON.encode(opened)).decode()["round_id"] == opened["round_id"]
    await first.publish(bot, -100, "chess")
    state = await first.current(-100, "chess")
    await first.save(-100, "chess", state)
    bot.edit_error = BadRequest("Message is not modified")
    restarted = BoardPanelService(repo)
    assert await restarted.publish(bot, -100, "chess")
    assert not (await restarted.current(-100, "chess"))["board_panel_dirty"]
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_retry_after_keeps_panel_dirty_with_bounded_retry_time(monkeypatch):
    monkeypatch.setattr("src.services.board_panel_service.time.time", lambda: 100.0)
    repo, bot = Repository(), Bot()
    service = BoardPanelService(repo)
    await service.save(-100, "connect4", fresh("connect4"))
    await service.publish(bot, -100, "connect4")
    state = await service.current(-100, "connect4")
    await service.save(-100, "connect4", state)
    bot.edit_error = RetryAfter(7)
    assert not await service.publish(bot, -100, "connect4")
    delayed = await service.current(-100, "connect4")
    assert delayed["board_panel_dirty"]
    assert 101 <= delayed["board_panel_retry_at"] <= 400
    assert not await service.publish(bot, -100, "connect4")
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_legacy_missing_sink_markers_remain_fail_closed_after_save():
    repo, ledger, bot = Repository(), Ledger(), Bot()
    funded = BoardPanelService(repo, leaderboard=ledger, economy=ledger)
    opened = await funded.save(-100, "connect4", fresh("connect4"))
    opened["players"].append({"id": 2, "name": "Iki", "token": "🟡"})
    opened.update(status="won", winner_id=1)
    await funded.save(-100, "connect4", opened)
    repo.rows[(-100, "connect4")].pop("leaderboard_required")
    repo.rows[(-100, "connect4")].pop("economy_required")
    without_coins = BoardPanelService(repo, leaderboard=ledger)
    same_round = await without_coins.current(-100, "connect4")
    saved = await without_coins.save(-100, "connect4", same_round)
    assert "economy_required" not in saved
    with pytest.raises(RuntimeError, match="board_score_store_unavailable"):
        await without_coins.process(bot, -100, "connect4")
    assert (await without_coins.current(-100, "connect4"))["board_score_pending"]


@pytest.mark.asyncio
async def test_uncertain_send_can_adopt_its_real_callback_panel_without_duplicate():
    repo, bot = Repository(), Bot()
    service = BoardPanelService(repo)
    opened = await service.save(-100, "connect4", fresh("connect4"))
    bot.send_error = TimedOut()
    assert not await service.publish(bot, -100, "connect4")
    assert await service.adopt_callback(-100, "connect4", 77, opened["round_id"])
    bot.send_error = None
    assert await service.publish(bot, -100, "connect4")
    live = await service.current(-100, "connect4")
    assert live["prompt_message_id"] == 77
    assert len(bot.sent) == 0 and len(bot.edits) == 1


@pytest.mark.asyncio
async def test_initial_rate_limit_retries_automatically_after_full_server_delay(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("src.services.board_panel_service.time.time", lambda: clock[0])
    repo, bot = Repository(), Bot()
    service = BoardPanelService(repo)
    await service.save(-100, "connect4", fresh("connect4"))
    bot.send_error = RetryAfter(600)
    assert not await service.publish(bot, -100, "connect4")
    delayed = await service.current(-100, "connect4")
    assert delayed["board_panel_retry_at"] == 700.0
    assert not delayed["board_panel_delivery_unknown"]
    clock[0] = 699.0
    assert not await service.publish(bot, -100, "connect4")
    bot.send_error = None
    clock[0] = 701.0
    assert await service.publish(bot, -100, "connect4")
    assert not (await service.current(-100, "connect4"))["board_panel_dirty"]
