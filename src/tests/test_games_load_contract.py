"""Small deterministic load contract for the in-memory game recovery store.

This is intentionally a domain stress test rather than a Telegram benchmark:
ten independent chat sessions perform the same bounded number of versioned
turn commits concurrently.  It catches accidental global state, aliasing and
lost updates without depending on a live service.
"""

from __future__ import annotations

import asyncio

import pytest

from src.games.catalog import GameSession, get_game_definition
from src.games.state_store import InMemoryGameStateStore


@pytest.mark.asyncio
async def test_ten_concurrent_sessions_commit_all_turns_and_recover_snapshot():
    store = InMemoryGameStateStore(max_sessions=20)
    sessions = [
        GameSession.create(
            get_game_definition("wordle_tr"),
            chat_id=-50_000 - index,
            owner_id=10_000 + index,
            state={"round": index, "moves": 0},
        )
        for index in range(10)
    ]
    for session in sessions:
        assert await store.create(session)

    async def run_session(session_id: str, turns: int = 20) -> int:
        committed = 0
        for _ in range(turns):
            current = await store.get(session_id)
            assert current is not None
            payload = dict(current.state)
            payload["moves"] = int(payload.get("moves", 0)) + 1
            candidate = current.with_updates(state=payload, combo=payload["moves"], version=current.version + 1)
            assert await store.save(candidate, expected_version=current.version)
            committed += 1
            await asyncio.sleep(0)
        return committed

    committed = await asyncio.gather(*(run_session(session.session_id) for session in sessions))
    assert committed == [20] * 10

    snapshot = await store.snapshot()
    assert len(snapshot) == 10
    assert all(row["version"] == 20 for row in snapshot)
    assert all(row["state"]["moves"] == 20 for row in snapshot)

    recovered = InMemoryGameStateStore(max_sessions=20)
    assert await recovered.restore(snapshot) == 10
    for session in sessions:
        loaded = await recovered.get(session.session_id)
        assert loaded is not None
        assert loaded.version == 20
        assert loaded.state["moves"] == 20


@pytest.mark.asyncio
async def test_load_recovery_rejects_duplicate_chat_game_key_atomically():
    store = InMemoryGameStateStore(max_sessions=20)
    first = GameSession.create(get_game_definition("wordle_tr"), chat_id=-100, owner_id=11, state={"round": 1})
    second = GameSession.create(get_game_definition("wordle_tr"), chat_id=-100, owner_id=12, state={"round": 2})
    assert await store.create(first)
    assert await store.create(second) is False

    baseline = await store.snapshot()
    malformed = [*baseline, second.to_dict()]
    with pytest.raises(ValueError, match="game_state_snapshot_duplicate"):
        await store.restore(malformed)
    assert await store.snapshot() == baseline
