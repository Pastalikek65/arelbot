from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from copy import deepcopy

import pytest

from src.features.contracts import FeatureDefinition
from src.games.catalog import GameSession, get_game_definition
from src.games.gomoku import apply_move, create_game
from src.games.state_store import InMemoryGameStateStore


def test_board_definitions_publish_versioned_state_metadata():
    xox = get_game_definition("tictactoe")
    connect4 = get_game_definition("connect4")

    assert (xox.schema_version, xox.state_key, xox.board_size, xox.connect_length) == (1, "tictactoe", (6, 6), 4)
    assert (connect4.schema_version, connect4.state_key, connect4.board_size, connect4.connect_length) == (1, "connect4", (6, 7), 4)

    session = GameSession.create(xox, chat_id=-100, owner_id=7, players=[7, 8])
    record = session.to_dict()
    assert record["schema_version"] == 1
    assert record["state_key"] == "tictactoe"
    assert record["board_size"] == [6, 6]
    assert record["connect_length"] == 4
    assert GameSession.from_dict(record).storage_key == "game:-100:tictactoe"


def test_new_board_and_word_games_publish_durable_metadata():
    gomoku = get_game_definition("gomoku")
    wordle = get_game_definition("wordle_tr")

    assert (gomoku.schema_version, gomoku.state_key, gomoku.board_size, gomoku.connect_length) == (
        1,
        "gomoku",
        (10, 10),
        5,
    )
    assert (wordle.schema_version, wordle.state_key, wordle.board_size, wordle.connect_length) == (
        1,
        "wordle_tr",
        None,
        None,
    )
    assert gomoku.min_players == gomoku.max_players == 2
    assert wordle.min_players == wordle.max_players == 1


@pytest.mark.asyncio
async def test_gomoku_snapshot_recovers_board_state_without_aliasing():
    gomoku = create_game(chat_id=-100, players=(7, 8), board_size=10, game_id="round-1")
    moved = apply_move(gomoku, player_id=7, row=2, column=3, action_id="tg:1")
    assert moved.ok
    session = GameSession.create(
        get_game_definition("gomoku"),
        chat_id=-100,
        owner_id=7,
        players=[7, 8],
        state=moved.state.to_dict(),
    )
    source = InMemoryGameStateStore()
    assert await source.create(session)

    recovered = InMemoryGameStateStore()
    assert await recovered.restore(await source.snapshot()) == 1
    loaded = await recovered.get_for_chat(-100, "gomoku")
    assert loaded is not None
    assert loaded.storage_key == "game:-100:gomoku"
    assert loaded.state["board"][23] == 1

    # A caller can mutate its copy without changing the recovered store.
    copied = dict(loaded.state)
    copied["board"] = list(copied["board"])
    copied["board"][23] = 2
    assert (await recovered.get_for_chat(-100, "gomoku")).state["board"][23] == 1


@pytest.mark.asyncio
async def test_wordle_restart_recovery_preserves_single_player_payload_and_cas():
    state = {
        "puzzle_id": "2026-10-17",
        "answer_hash": "a" * 64,
        "guess_rows": [{"guess": "kalem", "result": "⬛🟩⬛⬛⬛"}],
        "attempts_left": 5,
        "mode": "daily",
    }
    session = GameSession.create(
        get_game_definition("wordle_tr"),
        chat_id=-100,
        owner_id=7,
        state=state,
    )
    source = InMemoryGameStateStore()
    assert await source.create(session)
    loaded = await source.get(session.session_id)
    assert loaded is not None
    changed = loaded.with_updates(state={**loaded.state, "attempts_left": 4}, version=1)
    assert await source.save(changed, expected_version=0)
    assert await source.save(changed.with_updates(state={**changed.state, "attempts_left": 3}), expected_version=0) is False

    restored = InMemoryGameStateStore()
    assert await restored.restore(await source.snapshot()) == 1
    recovered = await restored.get_for_chat(-100, "wordle_tr")
    assert recovered is not None
    assert recovered.state["puzzle_id"] == "2026-10-17"
    assert recovered.state["attempts_left"] == 4


def test_legacy_tictactoe_snapshot_remains_readable_after_board_upgrade():
    session = GameSession.create(get_game_definition("tictactoe"), chat_id=-100, owner_id=7, players=[7, 8])
    payload = session.to_dict()
    payload["board_size"] = [3, 3]
    payload["connect_length"] = 3
    recovered = GameSession.from_dict(payload)
    assert recovered.board_size == (3, 3)
    assert recovered.connect_length == 3


@pytest.mark.parametrize(
    ("game_key", "board_size", "connect_length"),
    [("tictactoe", (7, 7), 4), ("tictactoe", (8, 8), 4), ("gomoku", (12, 12), 5), ("gomoku", (15, 15), 5)],
)
def test_selectable_board_rounds_survive_restart_metadata_validation(game_key, board_size, connect_length):
    session = GameSession.create(
        get_game_definition(game_key),
        chat_id=-100,
        owner_id=7,
        players=[7, 8],
    ).with_updates(board_size=board_size, connect_length=connect_length)
    recovered = GameSession.from_dict(session.to_dict())
    assert recovered.board_size == board_size
    assert recovered.connect_length == connect_length


@pytest.mark.parametrize(
    ("game_key", "board_size"),
    [("tictactoe", (5, 5)), ("tictactoe", (6, 7)), ("gomoku", (5, 5)), ("gomoku", (16, 16))],
)
def test_invalid_selectable_board_rounds_are_rejected(game_key, board_size):
    session = GameSession.create(
        get_game_definition(game_key),
        chat_id=-100,
        owner_id=7,
        players=[7, 8],
    ).with_updates(board_size=board_size)
    with pytest.raises(ValueError, match="game_state_board_size_mismatch"):
        GameSession.from_dict(session.to_dict())


def test_feature_definition_exposes_durable_board_metadata():
    definition = FeatureDefinition(
        key="tictactoe",
        category="oyun",
        provider_key="local_game",
        cache_ttl_seconds=0,
        freshness_sla_seconds=0,
        timeout_ms=1000,
        audit_policy="game_state",
        schema_version=1,
        state_key="tictactoe",
        board_size=(6, 6),
        connect_length=4,
    )
    assert definition.state_key == "tictactoe"
    assert definition.board_size == (6, 6)
    assert definition.connect_length == 4


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_id", ""),
        ("players", []),
        ("state_key", "connect4"),
        ("board_size", [4, 4]),
        ("schema_version", 99),
    ],
)
def test_malformed_or_mismatched_game_snapshot_is_rejected(field, value):
    session = GameSession.create(get_game_definition("tictactoe"), chat_id=-100, owner_id=7, players=[7, 8])
    payload = session.to_dict()
    payload[field] = value
    with pytest.raises(ValueError):
        GameSession.from_dict(payload)


@pytest.mark.asyncio
async def test_store_cas_is_atomic_and_reads_are_defensive():
    store = InMemoryGameStateStore()
    session = GameSession.create(get_game_definition("tictactoe"), chat_id=-100, owner_id=7, players=[7, 8])
    assert await store.create(session)
    first = await store.get(session.session_id)
    second = await store.get(session.session_id)
    assert first is not second
    assert first is not None and second is not None

    async def write(candidate, combo):
        return await store.save(candidate.with_updates(combo=combo, version=1), expected_version=0)

    results = await asyncio.gather(write(first, 1), write(second, 2))
    assert sorted(results) == [False, True]
    assert (await store.get(session.session_id)).combo in {1, 2}


@pytest.mark.asyncio
async def test_snapshot_recovery_is_atomic_and_duplicate_state_keys_are_blocked():
    store = InMemoryGameStateStore()
    session = GameSession.create(get_game_definition("tictactoe"), chat_id=-100, owner_id=7, players=[7, 8])
    assert await store.create(session)
    duplicate = GameSession.create(get_game_definition("tictactoe"), chat_id=-100, owner_id=9, players=[9, 10])
    assert await store.create(duplicate) is False

    snapshot = await store.snapshot()
    recovered = InMemoryGameStateStore()
    assert await recovered.restore(deepcopy(snapshot)) == 1
    loaded = await recovered.get_for_chat(-100, "tictactoe")
    assert loaded is not None and loaded.session_id == session.session_id

    malformed = deepcopy(snapshot)
    malformed[0]["board_size"] = [4, 4]
    with pytest.raises(ValueError):
        await recovered.restore(malformed)
    assert await recovered.get(session.session_id) is not None


@pytest.mark.asyncio
async def test_expired_session_survives_recovery_as_expired():
    now = datetime.now(timezone.utc)
    session = GameSession.create(get_game_definition("hangman"), chat_id=-100, owner_id=7, players=[7], now=now - timedelta(seconds=10))
    expired = session.with_updates(expires_at=now - timedelta(seconds=1))
    store = InMemoryGameStateStore()
    assert await store.restore([expired.to_dict()]) == 1
    loaded = await store.get(expired.session_id)
    assert loaded is not None and loaded.is_expired(now)
