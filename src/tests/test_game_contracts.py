from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.games.action_ledger import GameActionLedger
from src.games.catalog import GameDefinition, GameSession, get_game_definition
from src.games.engine import TurnValidationCode, calculate_score, combo_multiplier, next_combo, validate_turn
from src.games.state_store import InMemoryGameStateStore


def test_game_catalog_exposes_typed_definition_and_session_contract():
    definition = get_game_definition("word_game")

    assert isinstance(definition, GameDefinition)
    assert definition.key == "word_game"
    session = GameSession.create(definition, chat_id=-100, owner_id=7, players=[7, 8])
    assert session.status == "active"
    assert session.turn_user_id == 7
    assert session.expires_at > session.started_at


def test_turn_validation_rejects_wrong_player_duplicate_and_expired_turns():
    definition = get_game_definition("word_game")
    session = GameSession.create(definition, chat_id=-100, owner_id=7, players=[7, 8])

    assert validate_turn(session, user_id=8, action_id="a1").code is TurnValidationCode.NOT_YOUR_TURN
    session = session.advance_turn(8)
    assert validate_turn(session, user_id=7, action_id="a2").code is TurnValidationCode.NOT_YOUR_TURN
    assert validate_turn(session, user_id=8, action_id="a2").valid
    session = session.record_action("a2")
    assert validate_turn(session, user_id=8, action_id="a2").code is TurnValidationCode.DUPLICATE_ACTION

    expired = session.with_updates(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert validate_turn(expired, user_id=8, action_id="a3").code is TurnValidationCode.EXPIRED


def test_combo_and_generic_score_contract_respects_timeout_and_difficulty():
    assert next_combo(2, success=True) == 3
    assert next_combo(2, success=False) == 0
    assert combo_multiplier(4) > combo_multiplier(1)
    assert calculate_score(20, difficulty="expert", combo=3) > calculate_score(20, difficulty="easy")
    assert calculate_score(20, timed_out=True) == 0


@pytest.mark.asyncio
async def test_state_store_uses_versioned_compare_and_set_for_sessions():
    store = InMemoryGameStateStore()
    session = GameSession.create(get_game_definition("hangman"), chat_id=-100, owner_id=7, players=[7])

    await store.create(session)
    loaded = await store.get(session.session_id)
    assert loaded and loaded.version == 0
    updated = loaded.with_updates(version=1, combo=2)
    assert await store.save(updated, expected_version=0) is True
    assert await store.save(updated.with_updates(combo=3), expected_version=0) is False
    assert (await store.get(session.session_id)).combo == 2


@pytest.mark.asyncio
async def test_action_ledger_run_releases_failed_action_and_replays_success():
    ledger = GameActionLedger()
    calls = []

    async def operation():
        calls.append(1)
        return "ok"

    first = await ledger.run(-100, "word_game", "turn-1", operation)
    second = await ledger.run(-100, "word_game", "turn-1", operation)
    assert first.executed is True and first.result == "ok"
    assert second.replayed is True and second.executed is False
    assert calls == [1]

