"""Private-role state survives the real BSON codec and restart."""

from bson import BSON
import pytest

from src.games import vampire
from src.services.vampire_service import VampireService
from src.tests.test_vampire_service import Repository


class BsonRepository(Repository):
    async def create_game_state_if_absent(self, chat, key, state):
        return await super().create_game_state_if_absent(chat, key, BSON.encode(state).decode())

    async def compare_and_save_game_state(self, chat, key, state, *, expected_version):
        return await super().compare_and_save_game_state(
            chat, key, BSON.encode(state).decode(), expected_version=expected_version)


@pytest.mark.asyncio
async def test_restart_midnight_preserves_secret_actions_and_deadline():
    repo = BsonRepository()
    service = VampireService(repo)
    state = vampire.new_lobby(chat_id=-100, thread_id=9, owner_id=1,
                              owner_name="Bir", round_id="r", now=1)
    for uid in range(2, 8):
        state = vampire.join(state, user_id=uid, user_name=str(uid), now=2)
    for uid in range(1, 8):
        state = vampire.ready(state, user_id=uid, now=3)
    state = vampire.start(state, user_id=1,
        roles=["vampire", "seer", "healer", "villager", "villager", "villager", "villager"], now=4)
    for uid in range(1, 8):
        state = vampire.role_ack(state, user_id=uid, now=5)
    state = vampire.activate(state, now=10)
    repo.rows[(-100, service.key(9))] = BSON.encode(state).decode()
    await service.act(chat_id=-100, thread_id=9, user_id=1, action_id="n1",
                      action="night", target_id=4, round_id="r", phase_seq=state["phase_seq"], now=11)
    restarted = VampireService(repo)
    restored = await restarted.current(-100, 9)
    assert restored["actions"]["1"] == 4 and restored["deadline"] == 100
    assert all("role" not in p for p in vampire.public_state(restored)["players"])
    await restarted.advance(chat_id=-100, thread_id=9, round_id="r", now=100)
    assert (await restarted.current(-100, 9))["last_deaths"] == [4]
