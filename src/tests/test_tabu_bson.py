"""Use the production BSON codec on every durable Tabu transition."""
from bson import BSON
import pytest

from src.services.tabu_service import TabuService
from src.tests.test_tabu_service import Repository


class BsonRepository(Repository):
    async def create_game_state_if_absent(self, chat, key, state):
        return await super().create_game_state_if_absent(chat, key, BSON.encode(state).decode())

    async def compare_and_save_game_state(self, chat, key, state, *, expected_version):
        return await super().compare_and_save_game_state(chat, key, BSON.encode(state).decode(), expected_version=expected_version)


@pytest.mark.asyncio
async def test_full_entropy_deck_survives_real_bson_lobby_and_card_start(monkeypatch):
    seed = (1 << 127) + 73571
    monkeypatch.setattr("src.services.tabu_service.secrets.randbits", lambda bits: seed)
    service = TabuService(BsonRepository())
    _, state = await service.open(chat_id=-100, user_id=1, user_name="A")
    assert int(state["deck_seed"]) == seed
    for uid, team in [(2,"blue"),(3,"red"),(4,"blue")]:
        await service.act(chat_id=-100,user_id=uid,user_name=str(uid),action_id=f"join:{uid}",action="join",team=team)
    for uid in range(1,5):
        payload = await service.mint_invite(chat_id=-100,user_id=uid,round_id=state["round_id"])
        await service.accept_invite(payload,user_id=uid)
    await service.act(chat_id=-100,user_id=1,action_id="start",action="start")
    restored = await service.current(-100)
    assert restored["phase"] == "card_delivery" and restored["card"]
    assert int(restored["deck_seed"]) == seed
