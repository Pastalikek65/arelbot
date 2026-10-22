"""Isolated Mongo + synthetic Telegram load exercise for four legacy boards.

This is a test-profile probe, never production traffic. It creates random
collections in arel_bot_test and drops them even when an assertion fails.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.motor_asyncio import AsyncIOMotorClient
from telegram.error import BadRequest, RetryAfter, TimedOut

from config import Config
from src.database import Database
from src.games.action_ledger import GameActionLedger
from src.games.chess import chess_click, chess_join, chess_start, new_chess_state
from src.services.gomoku_ui import gomoku_join, gomoku_move, gomoku_start, new_gomoku_state
from src.services.tictactoe_ui import new_tictactoe_state, tictactoe_join, tictactoe_move, tictactoe_start
from src.services.word_game_ui import connect4_drop, connect4_join, connect4_start, new_connect4_state

KINDS = ("connect4", "tictactoe", "gomoku", "chess")
MOVE_COLUMNS = (0, 1, 2, 3, 4, 5, 6, 0)
MOVE_CELLS = (0, 7, 14, 21, 2, 9, 16, 23)
CHESS_CLICKS = ("e2", "e4", "e7", "e5", "g1", "f3", "b8", "c6")
TABLES_PER_KIND = 25
STEPS_PER_TABLE = 10


@dataclass(frozen=True)
class Table:
    chat_id: int
    game_key: str
    owner_id: int
    opponent_id: int

    @property
    def action_prefix(self) -> str:
        return f"load:{self.game_key}:{self.chat_id}"


def make_state(table: Table) -> dict:
    common = {"owner_id": table.owner_id, "owner_name": "Oyuncu A", "chat_id": table.chat_id}
    if table.game_key == "connect4":
        return new_connect4_state(**common)
    if table.game_key == "tictactoe":
        return new_tictactoe_state(**common, board_size=6)
    if table.game_key == "gomoku":
        return new_gomoku_state(**common, board_size=6)
    return new_chess_state(**common)


def legal_step(table: Table, state: dict, step: int) -> dict:
    """Apply a real legal domain transition; never fabricate board contents."""
    kind = table.game_key
    if step == 0:
        if kind == "connect4":
            outcome = connect4_join(state, user_id=table.opponent_id, user_name="Oyuncu B")
        elif kind == "tictactoe":
            outcome = tictactoe_join(state, user_id=table.opponent_id, user_name="Oyuncu B")
        elif kind == "gomoku":
            outcome = gomoku_join(state, user_id=table.opponent_id, user_name="Oyuncu B")
        else:
            outcome = chess_join(state, user_id=table.opponent_id, user_name="Oyuncu B")
    elif step == 1:
        if kind == "connect4":
            outcome = connect4_start(state)
        elif kind == "tictactoe":
            outcome = tictactoe_start(state)
        elif kind == "gomoku":
            outcome = gomoku_start(state)
        else:
            outcome = chess_start(state, user_id=table.owner_id)
    elif kind == "connect4":
        outcome = connect4_drop(state, user_id=state["current_player_id"],
                                column=MOVE_COLUMNS[step - 2])
    elif kind == "tictactoe":
        outcome = tictactoe_move(state, user_id=state["current_player_id"],
                                 cell=MOVE_CELLS[step - 2])
    elif kind == "gomoku":
        engine = state["engine"]
        actor = engine["players"][engine["turn_index"]]
        outcome = gomoku_move(state, user_id=actor,
                              cell=MOVE_CELLS[step - 2], action_id=f"load:{step}")
    else:
        outcome = chess_click(state, user_id=state["turn_user_id"],
                              square=CHESS_CLICKS[step - 2])
    if not outcome.get("changed"):
        raise AssertionError(f"invalid_domain_transition:{kind}:{step}:{outcome.get('status')}")
    return outcome


class SyntheticTelegram:
    """Bounded in-memory transport with deterministic Telegram-like faults."""

    def __init__(self):
        self.messages: dict[tuple[int, int], tuple[str, object]] = {}
        self._counter = 0
        self._next_id = 1
        self._planned: dict[int, str] = {}
        self.faults = {"rate_limit": 0, "timeout": 0, "deleted": 0}

    def inject(self, chat_id: int, fault: str) -> None:
        self._planned[int(chat_id)] = fault

    async def _delay(self):
        self._counter += 1
        await asyncio.sleep(0.003 + (self._counter % 4) * 0.002)

    async def send_message(self, *, chat_id, text, reply_markup=None, **_kwargs):
        await self._delay()
        fault = self._planned.pop(int(chat_id), None)
        if fault == "rate_limit":
            self.faults["rate_limit"] += 1
            raise RetryAfter(retry_after=1)
        message_id = self._next_id
        self._next_id += 1
        self.messages[(int(chat_id), message_id)] = (text, reply_markup)
        if fault == "timeout":
            self.faults["timeout"] += 1
            raise TimedOut()
        return SimpleNamespace(message_id=message_id)

    async def edit_message_text(self, *, chat_id, message_id, text, reply_markup=None, **_kwargs):
        await self._delay()
        key = (int(chat_id), int(message_id))
        fault = self._planned.pop(int(chat_id), None)
        if fault == "deleted":
            self.messages.pop(key, None)
            self.faults["deleted"] += 1
            raise BadRequest("Message to edit not found")
        if key not in self.messages:
            raise BadRequest("Message to edit not found")
        if fault == "rate_limit":
            self.faults["rate_limit"] += 1
            raise RetryAfter(retry_after=1)
        unchanged = self.messages[key] == (text, reply_markup)
        self.messages[key] = (text, reply_markup)
        if fault == "timeout":
            self.faults["timeout"] += 1
            raise TimedOut()
        if unchanged:
            raise BadRequest("Message is not modified")
        return SimpleNamespace(message_id=message_id)


def percentile(values: list[float], percentage: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentage / 100
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


async def main() -> dict:
    parsed = urlsplit(Config.MONGODB_URI)
    if (os.getenv("APP_ENV") != "test" or parsed.path != "/arel_bot_test"
            or parsed.username != "arel_test"):
        raise RuntimeError("synthetic_test_database_required")

    from src.services.board_panel_service import BoardPanelService

    client = AsyncIOMotorClient(Config.MONGODB_URI, serverSelectionTimeoutMS=5000)
    database = client["arel_bot_test"]
    identity = uuid4().hex
    games = database[f"board_load_games_{identity}"]
    actions = database[f"board_load_actions_{identity}"]

    class Scope:
        available = True

        def __getitem__(self, name):
            if name == "game_states":
                return games
            if name == "game_action_ledger":
                return actions
            raise RuntimeError("unexpected_collection")

    previous = Database._db
    Database._db = Scope()
    repo = Database.__new__(Database)
    bot = SyntheticTelegram()
    services = (BoardPanelService(repo), BoardPanelService(repo))
    ledgers = (GameActionLedger(repo), GameActionLedger(repo))
    tables = tuple(Table(-100_000 - index, KINDS[index % 4],
                         10_000 + 2 * index, 10_001 + 2 * index)
                   for index in range(TABLES_PER_KIND * len(KINDS)))
    latencies: list[float] = []
    duplicate_rejections = 0
    failures = 0
    started = time.perf_counter()
    try:
        await games.create_index([("chat_id", 1), ("game_type", 1)], unique=True)
        await actions.create_index("action_key", unique=True)
        for table in tables:
            await repo.save_game_state(table.chat_id, table.game_key, make_state(table))

        async def play(table: Table):
            nonlocal duplicate_rejections, failures
            for step in range(STEPS_PER_TABLE):
                action_id = f"{table.action_prefix}:{step}"

                async def attempt(index: int, step=step, action_id=action_id):
                    service = services[index]
                    start = time.perf_counter()

                    async def operation():
                        async with service.guard(table.chat_id, table.game_key):
                            state = await service.current(table.chat_id, table.game_key)
                            legal_step(table, state, step)
                            await service.save(table.chat_id, table.game_key, state)

                    result = await ledgers[index].run(table.chat_id, table.game_key,
                                                      action_id, operation)
                    latencies.append((time.perf_counter() - start) * 1000)
                    return result

                first, second = await asyncio.gather(attempt(0), attempt(1))
                if sorted((first.executed, second.executed)) != [False, True]:
                    failures += 1
                    raise AssertionError(f"duplicate_accepted:{table.game_key}:{step}")
                duplicate_rejections += 1
                # Public panel work uses the same canonical message after
                # successful delivery; transient faults remain retryable.
                await services[step % 2].publish(bot, table.chat_id, table.game_key)

        await asyncio.wait_for(asyncio.gather(*(play(table) for table in tables)), timeout=180)
        restarted = BoardPanelService(repo)
        # Exercise explicit delivery recovery on a new service instance. An
        # ambiguous send stays closed until the known synthetic message is
        # adopted; a rate limit/timeout retries after its stored deadline.
        recovery = tables[:4]
        previous_deleted_message = (await restarted.current(
            recovery[2].chat_id, recovery[2].game_key))["prompt_message_id"]
        for table in recovery:
            async with restarted.guard(table.chat_id, table.game_key):
                state = await restarted.current(table.chat_id, table.game_key)
                if table is recovery[3]:
                    state["prompt_message_id"] = 0
                await restarted.save(table.chat_id, table.game_key, state)
        for table, fault in zip(recovery, ("rate_limit", "timeout", "deleted", "timeout")):
            bot.inject(table.chat_id, fault)
            await restarted.publish(bot, table.chat_id, table.game_key)
        unknown_table = recovery[3]
        unknown = await restarted.current(unknown_table.chat_id, unknown_table.game_key)
        assert unknown["board_panel_delivery_unknown"] and not unknown["prompt_message_id"]
        assert not await restarted.process(bot, unknown_table.chat_id, unknown_table.game_key)
        delivered_ids = [mid for chat, mid in bot.messages if chat == unknown_table.chat_id]
        assert delivered_ids
        assert await restarted.adopt_callback(unknown_table.chat_id, unknown_table.game_key,
            max(delivered_ids), unknown["round_id"])
        await asyncio.sleep(5.1)
        for _ in range(3):
            await asyncio.wait_for(asyncio.gather(*(restarted.process(bot, table.chat_id,
                table.game_key) for table in tables)), timeout=60)
        deleted_state = await restarted.current(recovery[2].chat_id, recovery[2].game_key)
        assert deleted_state["prompt_message_id"] != previous_deleted_message
        assert not await restarted.adopt_callback(recovery[2].chat_id, recovery[2].game_key,
            previous_deleted_message, deleted_state["round_id"])
        assert not await restarted.adopt_callback(recovery[0].chat_id, recovery[0].game_key,
            999_999, "stale-round")
        for table in tables:
            state = await restarted.current(table.chat_id, table.game_key)
            move_count = (len(state.get("uci_moves") or []) if table.game_key == "chess"
                          else int(state.get("moves") or 0))
            if state.get("status") != "playing" or move_count != (4 if table.game_key == "chess" else 8):
                failures += 1
                raise AssertionError(f"lost_or_duplicate_move:{table.game_key}:{table.chat_id}")
            expected_revision = STEPS_PER_TABLE + int(table in recovery)
            if int(state.get("board_panel_revision") or 0) != expected_revision:
                failures += 1
                raise AssertionError("board_revision_lost_or_duplicated")
            if state.get("board_panel_dirty"):
                failures += 1
                raise AssertionError("board_delivery_still_pending")
            if not state.get("prompt_message_id"):
                failures += 1
                raise AssertionError("canonical_message_missing")
        # Distinct action IDs can also race from the same old snapshot. One
        # write must win and the stale writer must hit the Mongo CAS boundary.
        cas_table = Table(-700_001, "connect4", 800_001, 800_002)
        await repo.save_game_state(cas_table.chat_id, cas_table.game_key, make_state(cas_table))
        left, right = await asyncio.gather(*(service.current(
            cas_table.chat_id, cas_table.game_key) for service in services))
        legal_step(cas_table, left, 0)
        legal_step(cas_table, right, 0)

        async def stale_competitor(service, snapshot):
            async with service.guard(cas_table.chat_id, cas_table.game_key):
                return await service.save(cas_table.chat_id, cas_table.game_key, snapshot)

        cas_results = await asyncio.gather(stale_competitor(services[0], left),
                                           stale_competitor(services[1], right),
                                           return_exceptions=True)
        rejected = [index for index, value in enumerate(cas_results)
                    if isinstance(value, Exception)]
        if (sum(isinstance(value, dict) for value in cas_results) != 1 or
                len(rejected) != 1 or
                str(cas_results[rejected[0]]) not in
                {"board_state_conflict", "game_busy"}):
            raise AssertionError("board_stale_cas_not_rejected:" + ",".join(
                "ok" if isinstance(value, dict) else f"{type(value).__name__}:{value}"
                for value in cas_results))
        if str(cas_results[rejected[0]]) == "game_busy":
            try:
                await services[rejected[0]].save(cas_table.chat_id, cas_table.game_key,
                                                  (left, right)[rejected[0]])
            except RuntimeError as exc:
                if str(exc) != "board_state_conflict":
                    raise
            else:
                raise AssertionError("board_stale_cas_not_rejected_after_lease")
        cas_state = await restarted.current(cas_table.chat_id, cas_table.game_key)
        if (int(cas_state.get("board_panel_revision") or 0) != 1 or
                len(cas_state.get("players") or []) != 2):
            raise AssertionError("board_cas_corrupted_state")
        # The runtime fetches at most 100 due rows. A permanent backend error
        # in the oldest hundred must be deferred so later rows get a turn.
        from src.handlers.game_runtime import GameRuntime

        class BrokenFirstHundred:
            def __init__(self):
                self.healthy_seen: set[int] = set()

            async def process(self, _bot, chat_id, _game_key):
                if -500_101 < int(chat_id) <= -500_001:
                    raise RuntimeError("synthetic_score_backend_outage")
                if -500_141 < int(chat_id) <= -500_101:
                    self.healthy_seen.add(int(chat_id))

        base = datetime(2020, 1, 1, tzinfo=UTC)
        await games.insert_many([{
            "chat_id": -500_001 - index,
            "game_type": "connect4",
            "state": {"board_score_pending": True},
            "updated_at": base + timedelta(seconds=index),
        } for index in range(140)])
        runtime = GameRuntime(repo)
        sink = BrokenFirstHundred()
        runtime.board_panels = sink
        runtime_log = logging.getLogger("src.handlers.game_runtime")
        prior_level = runtime_log.level
        runtime_log.setLevel(logging.ERROR)
        try:
            await asyncio.wait_for(runtime.tick(SimpleNamespace(bot=bot)), timeout=60)
            if runtime.delivery_failures != 100 or sink.healthy_seen:
                raise AssertionError("runtime_first_page_not_isolated")
            await asyncio.wait_for(runtime.tick(SimpleNamespace(bot=bot)), timeout=60)
        finally:
            runtime_log.setLevel(prior_level)
        if len(sink.healthy_seen) != 40:
            raise AssertionError("runtime_due_page_starvation")
        deferred = await games.count_documents({"board_recovery_after": {"$gt": time.time()}})
        if deferred != 100:
            raise AssertionError("runtime_failed_rows_not_deferred")
        duration = max(0.001, time.perf_counter() - started)
        result = {"ok": failures == 0, "profile": "isolated_synthetic_mongo_and_telegram",
                  "real_telegram_load": False, "tables": len(tables),
                  "transitions": len(tables) * STEPS_PER_TABLE,
                  "duplicate_rejections": duplicate_rejections,
                  "failures": failures, "duration_seconds": round(duration, 3),
                  "throughput_transitions_per_second": round(
                      len(tables) * STEPS_PER_TABLE / duration, 2),
                  "action_latency_ms": {"p50": round(percentile(latencies, 50), 2),
                                        "p95": round(percentile(latencies, 95), 2),
                                        "p99": round(percentile(latencies, 99), 2)},
                  "synthetic_transport_faults": dict(bot.faults)}
        result["runtime_fairness"] = {"unhealthy_deferred": deferred,
                                      "later_healthy_processed": len(sink.healthy_seen),
                                      "tick_limit": 100}
        result["stale_cas_rejections"] = 1
        print(json.dumps(result, ensure_ascii=False))
        return result
    except Exception as exc:
        duration = max(0.001, time.perf_counter() - started)
        print(json.dumps({"ok": False, "profile": "isolated_synthetic_mongo_and_telegram",
            "real_telegram_load": False, "tables": len(tables),
            "committed_transitions": duplicate_rejections,
            "failures": failures + 1, "error_type": type(exc).__name__,
            "duration_seconds": round(duration, 3),
            "throughput_transitions_per_second": round(duplicate_rejections / duration, 2),
            "action_latency_ms": {"p50": round(percentile(latencies, 50), 2),
                                  "p95": round(percentile(latencies, 95), 2),
                                  "p99": round(percentile(latencies, 99), 2)},
            "synthetic_transport_faults": dict(bot.faults)}, ensure_ascii=False))
        raise
    finally:
        Database._db = previous
        await games.drop()
        await actions.drop()
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
