from types import SimpleNamespace

import pytest

from src.storage.state_scope import (
    get_scoped_state,
    pop_scoped_state,
    scope_state,
    set_scoped_state,
    state_matches,
)
from src.storage.state_store import MemoryStateStore, PersistentStateMap


@pytest.mark.asyncio
async def test_persistent_state_map_keeps_two_chat_wizards_for_one_user_separate():
    backend = MemoryStateStore()
    states = PersistentStateMap(ttl=900)
    states.bind(backend)

    states.set_for_scope(42, 1001, {"state": "awaiting_title", "data": {"value": "private"}})
    states.set_for_scope(42, -1001, {"state": "awaiting_title", "data": {"value": "group"}})
    await states.flush()

    assert states.get_for_scope(42, 1001)["data"]["value"] == "private"
    assert states.get_for_scope(42, -1001)["data"]["value"] == "group"
    assert await backend.get("scope:42:1001")
    assert await backend.get("scope:42:-1001")

    pop_scoped_state(states, user_id=42, chat_id=1001)
    await states.flush()
    assert states.get_for_scope(42, 1001) is None
    assert states.get_for_scope(42, -1001)["data"]["value"] == "group"
    assert await backend.get("scope:42:1001") is None


def test_legacy_state_is_private_only_and_helpers_stamp_scope():
    legacy = {42: {"state": "awaiting_title"}}
    assert get_scoped_state(legacy, user_id=42, chat_id=42) == {"state": "awaiting_title"}
    assert get_scoped_state(legacy, user_id=42, chat_id=-1001) is None
    assert state_matches({"state": "x"}, user_id=42, chat_id=42)
    assert not state_matches({"state": "x"}, user_id=42, chat_id=-1001)

    set_scoped_state(legacy, user_id=42, chat_id=-1001, value={"state": "group"})
    assert legacy[42]["_scope"] == {"user_id": 42, "chat_id": -1001}
    assert get_scoped_state(legacy, user_id=42, chat_id=42) is None
    assert get_scoped_state(legacy, user_id=42, chat_id=-1001)["state"] == "group"


@pytest.mark.asyncio
async def test_hydrate_restores_scoped_keys_without_collapsing_user_states():
    backend = MemoryStateStore()
    await backend.set("scope:7:11", scope_state({"state": "one"}, user_id=7, chat_id=11), ttl=300)
    await backend.set("scope:7:-22", scope_state({"state": "two"}, user_id=7, chat_id=-22), ttl=300)
    states = PersistentStateMap(ttl=300)
    states.bind(backend)
    await states.hydrate()

    assert states.get_for_scope(7, 11)["state"] == "one"
    assert states.get_for_scope(7, -22)["state"] == "two"


@pytest.mark.asyncio
async def test_private_scope_helper_reads_legacy_user_record_during_rolling_upgrade():
    backend = MemoryStateStore()
    states = PersistentStateMap(ttl=300)
    states.bind(backend)
    states[9] = {"state": "legacy_private"}
    await states.flush()

    assert get_scoped_state(states, user_id=9, chat_id=9)["state"] == "legacy_private"
    assert get_scoped_state(states, user_id=9, chat_id=-99) is None


@pytest.mark.asyncio
async def test_sticker_delete_callback_cannot_consume_state_from_another_chat():
    from src.handlers.callback_handler import CallbackHandler

    class Query:
        data = "stpack:sticker_delete:42:abc123"

        def __init__(self):
            self.answers = []

        async def answer(self, *args, **kwargs):
            self.answers.append((args, kwargs))

        async def edit_message_text(self, *args, **kwargs):
            raise AssertionError("cross-chat state must not be edited")

    handler = CallbackHandler(SimpleNamespace(db=None), SimpleNamespace(owner_id=1), None)
    handler.user_states = {
        42: scope_state(
            {
                "state": "awaiting_sticker_delete_confirmation",
                "data": {"token": "abc123", "pack_name": "x", "file_id": "y"},
            },
            user_id=42,
            chat_id=111,
        )
    }
    query = Query()
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=222, type="private"),
    )
    await handler.handle_callback_query(update, SimpleNamespace(bot=None, chat_data={}))
    assert query.answers
    assert "sÃ¼resi" in str(query.answers[-1][0][0])
