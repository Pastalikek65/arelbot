"""Deterministic replay and failure-matrix tests for the game domain.

These tests deliberately stay below the Telegram/network boundary.  A game
callback can be delivered more than once, a worker can restart between moves,
and a client can tamper with a serialized payload.  The domain contracts must
make all three cases safe before a handler renders anything.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from src.games.action_ledger import GameActionLedger
from src.games.card_engine import (
    Card,
    CardAction,
    CardActionLedger,
    CardDrawStatus,
    CardGameState,
    Deck,
)
from src.games.catalog import GameSession, get_game_definition
from src.games.gomoku import (
    GomokuStatus,
    apply_move,
    create_game,
    replay_moves,
)
from src.games.state_store import InMemoryGameStateStore
from src.games.word_catalog import WordCatalog, WordEntry, normalize_turkish, safe_word_entry
from src.services.gomoku_ui import gomoku_join, gomoku_move, gomoku_start, new_gomoku_state


def _gomoku_state_with_moves():
    state = create_game(chat_id=-100, players=(11, 22), board_size=6, win_length=4, game_id="qa-round")
    for player, row, column, action in (
        (11, 0, 0, "cb-1"),
        (22, 1, 0, "cb-2"),
        (11, 0, 1, "cb-3"),
        (22, 1, 1, "cb-4"),
    ):
        result = apply_move(state, player_id=player, row=row, column=column, action_id=action)
        assert result.ok
        state = result.state
    return state


def test_gomoku_replay_is_byte_stable_and_rejects_board_tampering():
    state = _gomoku_state_with_moves()
    restored = type(state).from_dict(state.to_dict())

    assert restored.to_json() == state.to_json()
    assert restored.version == 4
    assert len(restored.moves) == 4

    tampered_board = deepcopy(state.to_dict())
    tampered_board["board"][0] = 2
    with pytest.raises(ValueError, match="invalid_gomoku_board"):
        type(state).from_dict(tampered_board)

    tampered_move = deepcopy(state.to_dict())
    tampered_move["moves"][1]["player_id"] = 11
    with pytest.raises(ValueError, match="invalid_gomoku_replay"):
        type(state).from_dict(tampered_move)


@pytest.mark.parametrize(
    "payload",
    [
        {"turn": 0, "player_id": 11, "row": 0, "column": 0, "action_id": "x"},
        {"turn": 1, "player_id": 11, "row": -1, "column": 0, "action_id": "x"},
        {"turn": 1, "player_id": 11, "row": 0, "column": 0},
        {"turn": 1, "player_id": 11, "row": 0, "column": 0, "action_id": ""},
    ],
)
def test_gomoku_replay_rejects_malformed_move_payloads(payload):
    with pytest.raises(ValueError):
        replay_moves(chat_id=-100, players=(11, 22), board_size=6, win_length=4, moves=[payload])


def test_gomoku_duplicate_action_does_not_mutate_state():
    state = create_game(chat_id=-100, players=(11, 22), board_size=6, win_length=4)
    accepted = apply_move(state, player_id=11, row=0, column=0, action_id="telegram-callback-1")
    assert accepted.status is GomokuStatus.MOVED
    replayed = apply_move(accepted.state, player_id=22, row=1, column=0, action_id="telegram-callback-1")
    assert replayed.status is GomokuStatus.DUPLICATE_ACTION
    assert replayed.state == accepted.state


def test_gomoku_ui_round_and_duplicate_callback_are_safe():
    state = new_gomoku_state(owner_id=11, owner_name="owner", chat_id=-100, board_size=6)
    assert gomoku_join(state, user_id=22, user_name="challenger")["changed"] is True
    assert gomoku_start(state)["status"] == "started"

    first = gomoku_move(state, user_id=11, cell=0, message_id=41, action_id="telegram:41")
    replay = gomoku_move(state, user_id=22, cell=6, message_id=42, action_id="telegram:41")
    assert first["changed"] is True
    assert replay["changed"] is False
    assert replay["status"] == "duplicate_action"
    assert state["moves"] == 1


def test_gomoku_ui_expired_turn_is_recoverable_without_sleep():
    state = new_gomoku_state(owner_id=11, owner_name="owner", chat_id=-100, board_size=6)
    gomoku_join(state, user_id=22, user_name="challenger")
    gomoku_start(state)
    state["turn_expires_at"] = 0
    result = gomoku_move(state, user_id=11, cell=0, action_id="expired-callback")
    assert result == {"status": "expired", "changed": True}
    assert state["status"] == "expired"


def test_card_game_replay_and_duplicate_action_contract():
    original = CardGameState.create(
        game_id="blackjack-qa",
        chat_id=-100,
        players=(11, 22),
        deck=Deck.standard(seed=7),
    )
    accepted = original.draw(player_id=11, action_id="draw-1", count=2)
    assert accepted.status is CardDrawStatus.ACCEPTED
    restored = CardGameState.from_dict(accepted.state.to_dict())
    assert restored.to_dict() == accepted.state.to_dict()

    replay = restored.draw(player_id=11, action_id="draw-1", count=2)
    assert replay.status is CardDrawStatus.REPLAYED
    assert replay.cards == ()
    assert replay.state == restored

    with pytest.raises(ValueError, match="card_action_conflict"):
        restored.draw(player_id=22, action_id="draw-1", count=1)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update({"schema_version": 99}),
        lambda payload: payload["deck"].update({"position": 10_000}),
        lambda payload: payload["hands"].update({"11": [{"rank": "JOKER", "suit": "X"}]}),
        lambda payload: payload.update({"players": [11, 11]}),
    ],
)
def test_card_state_rejects_malformed_or_tampered_payloads(mutator):
    state = CardGameState.create(game_id="card-qa", chat_id=-100, players=(11, 22), deck=Deck.standard(seed=3))
    payload = state.to_dict()
    mutator(payload)
    with pytest.raises(ValueError):
        CardGameState.from_dict(payload)


def test_card_deck_replay_is_deterministic_for_same_seed():
    first = CardGameState.create(game_id="a", chat_id=-100, players=(11,), deck=Deck.standard(seed=101))
    second = CardGameState.create(game_id="b", chat_id=-101, players=(11,), deck=Deck.standard(seed=101))
    first_cards = first.draw(player_id=11, action_id="a-1", count=4).cards
    second_cards = second.draw(player_id=11, action_id="b-1", count=4).cards
    assert tuple(card.code for card in first_cards) == tuple(card.code for card in second_cards)


def test_card_action_ledger_round_trip_rejects_duplicate_ids():
    action = CardAction(action_id="a1", kind="draw", player_id=11, payload={"count": 1})
    ledger = CardActionLedger((action,))
    restored = CardActionLedger.from_dict(ledger.to_dict())
    assert restored.contains("a1")
    with pytest.raises(ValueError, match="duplicate_card_action"):
        CardActionLedger((action, action))


def test_word_catalog_replay_is_seeded_and_hints_progress_without_answer_leaks():
    records = (
        WordEntry("alpha", "kalem", "genel", "kolay", "Yazı yazmak için kullanılan bir araç.", ("Okul çantasında bulunur.",)),
        WordEntry("beta", "masa", "genel", "kolay", "Üzerinde eşyaların durduğu düz yüzey.", ("Dört ayağı olabilir.",)),
    )
    first = WordCatalog(entries=records, seed=17)
    second = WordCatalog(entries=records, seed=17)
    a = first.draw(scope="chat:-100", category="genel", difficulty="kolay")
    b = second.draw(scope="chat:-100", category="genel", difficulty="kolay")
    assert a.entry.to_dict() == b.entry.to_dict()
    clues = first.hints_for(a.entry)
    assert len(clues) >= 5
    answer = normalize_turkish(a.entry.word)
    assert all(answer not in normalize_turkish(clue) for clue in clues)


def test_word_catalog_filters_malformed_and_unsafe_fixture_entries():
    unsafe = WordEntry("url", "https", "genel", "kolay", "http://example.test", ("kısa ipucu",))
    leaking = WordEntry("leak", "elma", "genel", "kolay", "Kırmızı elma.", ("Meyve",))
    valid = WordEntry("valid", "kalem", "genel", "kolay", "Yazı yazmak için kullanılan araç.", ("Okulda kullanılır.",))
    assert safe_word_entry(unsafe) is False
    assert safe_word_entry(leaking) is False
    catalog = WordCatalog(entries=(unsafe, leaking, valid), seed=1)
    assert [entry.key for entry in catalog.entries()] == ["valid"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", ""),
        ("chat_id", 0),
        ("owner_id", 0),
        ("status", "corrupted"),
        ("processed_actions", ["same", "same"]),
        ("started_at", "not-a-date"),
        ("state_key", "other-game"),
    ],
)
def test_game_session_from_dict_rejects_tampered_payloads(field, value):
    session = GameSession.create(get_game_definition("gomoku"), chat_id=-100, owner_id=11, players=[11, 22])
    payload = session.to_dict()
    payload[field] = value
    with pytest.raises(ValueError):
        GameSession.from_dict(payload)


@pytest.mark.asyncio
async def test_state_store_cas_allows_one_writer_from_ten_stale_readers():
    store = InMemoryGameStateStore()
    session = GameSession.create(get_game_definition("gomoku"), chat_id=-100, owner_id=11, players=[11, 22])
    assert await store.create(session)
    stale = [await store.get(session.session_id) for _ in range(10)]
    assert all(item is not None for item in stale)

    async def write(candidate, value):
        return await store.save(candidate.with_updates(combo=value, version=1), expected_version=0)

    results = await asyncio.gather(*(write(item, index) for index, item in enumerate(stale)))
    assert sum(results) == 1
    committed = await store.get(session.session_id)
    assert committed is not None and committed.version == 1 and 0 <= committed.combo < 10


@pytest.mark.asyncio
async def test_ten_sessions_snapshot_restore_is_atomic_and_defensive():
    source = InMemoryGameStateStore(max_sessions=20)
    sessions = [
        GameSession.create(get_game_definition("wordle_tr"), chat_id=-100 - index, owner_id=11 + index, state={"round": index})
        for index in range(10)
    ]
    created = [await source.create(session) for session in sessions]
    assert all(created)
    snapshot = await source.snapshot()
    restored = InMemoryGameStateStore(max_sessions=20)
    assert await restored.restore(snapshot) == 10
    assert len(await restored.list_for_chat(-100, status="active")) == 1

    malformed = deepcopy(snapshot)
    malformed[0]["state_key"] = "wrong"
    with pytest.raises(ValueError):
        await restored.restore(malformed)
    assert len(await restored.snapshot()) == 10


@pytest.mark.asyncio
async def test_action_ledger_deduplicates_ten_concurrent_callbacks():
    ledger = GameActionLedger()
    calls: list[int] = []

    async def operation():
        calls.append(1)
        await asyncio.sleep(0)
        return "committed"

    results = await asyncio.gather(*(
        ledger.run(-100, "gomoku", "cell:42", operation, user_id=11)
        for _ in range(10)
    ))
    assert sum(result.executed for result in results) == 1
    assert sum(result.replayed for result in results) == 9
    assert calls == [1]


@pytest.mark.asyncio
async def test_expired_session_round_trips_and_is_not_reactivated_on_restore():
    now = datetime.now(timezone.utc)
    session = GameSession.create(
        get_game_definition("gomoku"),
        chat_id=-100,
        owner_id=11,
        players=[11, 22],
        now=now - timedelta(minutes=10),
    ).with_updates(expires_at=now - timedelta(seconds=1))
    store = InMemoryGameStateStore()
    assert await store.restore([session.to_dict()]) == 1
    recovered = await store.get(session.session_id)
    assert recovered is not None
    assert recovered.status == "active"
    assert recovered.is_expired(now) is True


def test_game_catalog_contains_replayable_new_game_definitions():
    gomoku = get_game_definition("gomoku")
    wordle = get_game_definition("wordle_tr")
    assert gomoku.board_size == (10, 10)
    assert gomoku.connect_length == 5
    assert wordle.min_players == wordle.max_players == 1
