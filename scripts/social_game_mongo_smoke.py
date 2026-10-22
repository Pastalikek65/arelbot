"""Exercise actual Mongo query semantics in an isolated synthetic collection."""
from __future__ import annotations
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motor.motor_asyncio import AsyncIOMotorClient
from config import Config
from src.database import Database


async def main():
    uri = Config.MONGODB_URI
    parsed = urlsplit(uri)
    if os.getenv("APP_ENV") != "test" or parsed.path != "/arel_bot_test" or parsed.username != "arel_test":
        raise RuntimeError("synthetic_test_database_required")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    collection = client["arel_bot_test"]["social_smoke_" + uuid4().hex]
    class Scope:
        def __getitem__(self, key):
            if key != "game_states":
                raise RuntimeError("unexpected_collection")
            return collection
    previous = Database._db
    Database._db = Scope()
    database = Database.__new__(Database)
    clock = 1000.0
    rows = []
    def row(number, game, **state):
        rows.append({"chat_id": -number, "game_type": game, "state": state,
                     "updated_at": datetime.now(timezone.utc)})
    row(1,"casus",phase="question",deadline=1060)
    row(2,"casus",phase="delivery",deadline=1200,role_retry_at=1030,players=[{"id":7,"active":True,"delivered":False}])
    row(3,"casus",phase="delivery",deadline=1200,role_retry_at=0,players=[{"id":7,"active":True,"delivered":False}])
    row(4,"vampir_koylu",phase="night",deadline=None,panel_wait_until=999)
    row(5,"vampir_koylu",phase="night",private_dirty=True,private_retry_at=1030)
    row(6,"vampir_koylu",phase="finished",private_dirty=True,private_retry_at=0)
    row(7,"vampir_koylu",phase="finished",score_pending=True)
    row(8,"vampir_koylu",phase="cancelled",panel_dirty=False,private_dirty=False,score_pending=False)
    row(9,"casusevil",phase="question",deadline=999)
    row(10,"tabu_t7",phase="active",deadline=999)
    row(11,"casus_t42",phase="question",deadline=999,delivery_retry_at=1030)
    row(12,"casus_t42",phase="question",deadline=1060,players=[{"id":42,"active":True}],location="SECRET_SENTINEL",spy_id=42)
    row(13,"vampir_koylu",phase="finished",players=[{"id":43,"active":True}],role="SECRET_SENTINEL")
    row(21, "roulette", phase="finished", score_pending=True)
    row(22, "roulette", phase="finished", panel_dirty=True, panel_retry_at=999)
    row(23, "roulette", phase="finished", panel_dirty=True, panel_retry_at=1001)
    row(24, "blackjack:7", status="pending_bet")
    row(25, "blackjack:7", status="pending_double")
    row(26, "blackjack:8", status="finished", settlement_pending=True)
    row(27, "blackjack:8", status="cancelled", refund_pending=True)
    row(28, "blackjack:8", status="finished", terminal_panel_pending=True, panel_retry_at=999)
    row(29, "blackjack:8", status="finished", terminal_panel_pending=True, panel_retry_at=1001)
    row(30, "blackjack:evil", status="pending_bet")
    row(31, "roulette", phase="playing", turn_expires_at=999)
    row(32, "roulette", phase="playing", turn_expires_at=1001)
    row(33, "roulette", phase="expired", panel_dirty=True, panel_retry_at=999)
    row(34, "roulette", phase="expired", panel_dirty=True, panel_retry_at=1001)
    from src.games.puzzle_catalog import PUZZLES
    expected_puzzles = set()
    for index, puzzle in enumerate(PUZZLES, start=1):
        number = 100 + index * 10
        row(number, puzzle.key, phase="playing", deadline=999)
        row(number + 1, puzzle.key + "_t42", phase="playing", deadline=1001)
        row(number + 2, puzzle.key, phase="finished", score_pending=True)
        row(number + 3, puzzle.key, phase="playing", panel_wait_until=999)
        row(number + 4, puzzle.key, phase="lobby", lobby_expires_at=999)
        row(number + 5, puzzle.key, phase="playing", panel_dirty=True, delivery_retry_at=1001)
        row(number + 6, puzzle.key + "evil", phase="playing", deadline=999)
        row(number + 7, puzzle.key, phase="finished", deadline=999, score_pending=False)
        row(number + 8, puzzle.key, phase="cancelled", panel_wait_until=999, score_pending=False)
        row(number + 9, puzzle.key, phase="playing", private_dirty=True, private_retry_at=999)
        expected_puzzles.update({-number, -(number + 2), -(number + 3), -(number + 4), -(number + 9)})
        lease_case = 40000 + index * 10
        row(lease_case, puzzle.key, phase="playing", private_dirty=True,
            private_retry_at=999, delivery_retry_at=1030)
        row(lease_case + 1, puzzle.key + "_t42", phase="playing", deadline=999,
            delivery_retry_at=1030)
        row(lease_case + 2, puzzle.key, phase="finished", score_pending=True,
            delivery_retry_at=1030)
        row(lease_case + 3, puzzle.key, phase="playing", panel_dirty=True,
            delivery_retry_at=1030)
        row(lease_case + 4, puzzle.key, phase="playing", panel_wait_until=999,
            delivery_retry_at=1030)
        expected_puzzles.update({-lease_case, -(lease_case + 1), -(lease_case + 2), -(lease_case + 4)})
    # These documents are deliberately full of secrets. The routing methods
    # must return only allowlisted metadata, including for forum topics.
    from src.games.puzzle_catalog import SECRET_GAME_KEYS
    for index, key in enumerate(sorted(SECRET_GAME_KEYS), start=1):
        row(10000 + index, key + "_t42", phase="playing", players=[{"id":777,"active":True}],
            target="SECRET_SENTINEL", claims=["SECRET_SENTINEL"], private_panels={"777":{"secret":"SECRET_SENTINEL"}})
    rows.extend([
        {"chat_id":-9000,"game_type":"sozcuk_avi_t42","state":{
            "game_key":"sozcuk_avi","phase":"hunt","panel_message_id":500,
            "thread_id":42,"owner_id":888,"players":[{"id":888,"active":True,"name":"PRIVATE_NAME"}],
            "answers":["SECRET_SENTINEL"],"private_panels":{"888":{"secret":"SECRET_SENTINEL"}}}},
        {"chat_id":-9000,"game_type":"bomba_t43","state":{
            "game_key":"bomba","phase":"bomb_turn","players":[{"id":888,"active":True}]}},
        {"chat_id":-9000,"game_type":"yasak_harf_t42","state":{
            "game_key":"yasak_harf","phase":"clue","players":[{"id":888,"active":False}]}}
    ])
    try:
        await collection.insert_many(rows)
        due = await database.list_due_game_states(now=clock, limit=500)
        actual = {item["chat_id"] for item in due}
        assert actual == {-3,-4,-6,-7,-10,-21,-22,-24,-25,-26,-27,-28,-31,-33} | expected_puzzles, actual
        safe = await database.get_active_secret_game_for_user(42)
        assert safe == {"chat_id":-12,"game_type":"casus_t42"}, safe
        assert await database.get_active_secret_game_for_user(43) is None
        routes = await database.list_active_secret_game_routes(777)
        assert len(routes) == len(SECRET_GAME_KEYS)
        assert all(set(route) == {"chat_id","game_type"} for route in routes)
        public = await database.list_active_puzzle_routes(-9000,42,888)
        assert public == [{"game_type":"sozcuk_avi_t42","state":{
            "game_key":"sozcuk_avi","phase":"hunt","panel_message_id":500,
            "thread_id":42,"owner_id":888,"players":[{"id":888,"active":True}]}}], public
        assert "SECRET_SENTINEL" not in json.dumps(routes + public)
        print(json.dumps({"ok":True,"scope":"isolated_synthetic_mongo_collection","due_cases":len(rows),"selected":len(actual),"private_projection_safe":True,"secret_routes":len(routes),"batched_public_projection_safe":True}))
    finally:
        Database._db = previous
        await collection.drop()
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
