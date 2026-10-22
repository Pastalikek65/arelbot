"""CAS, lease, BSON and reward persistence for both grid puzzles."""

import asyncio
from copy import deepcopy

from bson import BSON
import pytest

from src.games import grid_puzzles as grid
from src.services.grid_puzzles_service import GridPuzzlesService
from src.services.grid_puzzles_ui import callback_data, keyboard, render


class Repository:
    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat, key):
        return deepcopy(self.rows.get((chat, key)))

    async def create_game_state_if_absent(self, chat, key, state):
        if (chat, key) in self.rows:
            return False
        self.rows[(chat, key)] = deepcopy(state)
        return True

    async def compare_and_save_game_state(self, chat, key, state, *, expected_version):
        old = self.rows.get((chat, key))
        if old is None or old["version"] != expected_version:
            return False
        assert state["version"] == expected_version + 1
        self.rows[(chat, key)] = deepcopy(state)
        return True


class BsonRepository(Repository):
    async def create_game_state_if_absent(self, chat, key, state):
        return await super().create_game_state_if_absent(chat, key, BSON.encode(state).decode())

    async def compare_and_save_game_state(self, chat, key, state, *, expected_version):
        return await super().compare_and_save_game_state(
            chat, key, BSON.encode(state).decode(), expected_version=expected_version)


class Leaderboard:
    def __init__(self):
        self.events = set()

    async def record_score(self, **kwargs):
        self.events.add(kwargs["event_id"])


@pytest.mark.asyncio
async def test_two_instances_same_pair_click_is_single_cas_action():
    repo = Repository()
    first, second = GridPuzzlesService(repo), GridPuzzlesService(repo)
    _, opened = await first.open(game_key=grid.MEMORY_KEY, chat_id=-100,
                                 user_id=1, user_name="Bir")
    _, existing = await second.open(game_key=grid.MEMORY_KEY, chat_id=-100,
                                    user_id=2, user_name="İki")
    assert opened["round_id"] == existing["round_id"]
    await first.act(game_key=grid.MEMORY_KEY, chat_id=-100, user_id=1,
                    action_id="start", action="start", round_id=opened["round_id"])
    state = await first.current(game_key=grid.MEMORY_KEY, chat_id=-100)
    state = grid.activate(state, now=10)
    state = grid.advance(state, now=18)
    state = grid.activate(state, now=20)
    repo.rows[(-100, grid.MEMORY_KEY)] = deepcopy(state)
    results = await asyncio.gather(
        first.act(game_key=grid.MEMORY_KEY, chat_id=-100, user_id=1,
                  action_id="a", action="pick", index=0, round_id=state["round_id"],
                  phase_seq=state["phase_seq"], turn_seq=state["turn_seq"], now=21),
        second.act(game_key=grid.MEMORY_KEY, chat_id=-100, user_id=1,
                   action_id="b", action="pick", index=0, round_id=state["round_id"],
                   phase_seq=state["phase_seq"], turn_seq=state["turn_seq"], now=21),
        return_exceptions=True)
    assert sum(isinstance(value, ValueError) for value in results) == 1
    assert (await first.current(game_key=grid.MEMORY_KEY, chat_id=-100))["first_pick"] == 0


@pytest.mark.asyncio
async def test_panel_lease_deadline_and_pending_reward_replacement():
    repo, board = Repository(), Leaderboard()
    service = GridPuzzlesService(repo, leaderboard=board)
    _, state = await service.open(game_key=grid.MINES_KEY, chat_id=-100,
                                  thread_id=7, user_id=1, user_name="Bir")
    claim = await service.claim_delivery(game_key=grid.MINES_KEY, chat_id=-100,
                                         thread_id=7, now=10)
    assert claim and await service.claim_delivery(game_key=grid.MINES_KEY,
        chat_id=-100, thread_id=7, now=11) is None
    assert (await service.current(game_key=grid.MINES_KEY, chat_id=-100,
                                  thread_id=7))["deadline"] is None
    await service.acknowledge_panel(game_key=grid.MINES_KEY, chat_id=-100,
        thread_id=7, round_id=state["round_id"], expected_version=claim["version"],
        message_id=42, now=12)
    active = await service.current(game_key=grid.MINES_KEY, chat_id=-100, thread_id=7)
    assert active["deadline"] == 612 and active["panel_message_id"] == 42
    assert await service.current(game_key=grid.MINES_KEY, chat_id=-100, thread_id=8) is None
    win = deepcopy(active)
    win.update(phase="finished", outcome="cleared", winner_ids=[1], score_pending=True)
    repo.rows[(-100, service.key(grid.MINES_KEY, 7))] = win
    with pytest.raises(ValueError, match="rewards_pending"):
        await service.open(game_key=grid.MINES_KEY, chat_id=-100,
                           thread_id=7, user_id=2, user_name="İki")
    await service.settle_scores(game_key=grid.MINES_KEY, chat_id=-100, thread_id=7)
    await service.settle_scores(game_key=grid.MINES_KEY, chat_id=-100, thread_id=7)
    assert board.events == {f"mayin_tarlasi:-100:{state['round_id']}:1"}
    _, fresh = await service.open(game_key=grid.MINES_KEY, chat_id=-100,
                                  thread_id=7, user_id=2, user_name="İki")
    assert fresh["round_id"] != state["round_id"]


@pytest.mark.asyncio
async def test_bson_restart_preserves_first_click_layout_and_scope():
    repo = BsonRepository()
    service = GridPuzzlesService(repo)
    _, state = await service.open(game_key=grid.MINES_KEY, chat_id=-100,
                                  user_id=1, user_name="Bir")
    claim = await service.claim_delivery(game_key=grid.MINES_KEY, chat_id=-100, now=10)
    await service.acknowledge_panel(game_key=grid.MINES_KEY, chat_id=-100,
        round_id=state["round_id"], expected_version=claim["version"],
        message_id=5, now=11)
    _, played = await service.act(game_key=grid.MINES_KEY, chat_id=-100,
        user_id=1, action_id="open:35", action="open", index=35,
        round_id=state["round_id"], now=12)
    restored = await GridPuzzlesService(repo).current(game_key=grid.MINES_KEY, chat_id=-100)
    assert restored["first_open"] == 35 and played["mines"] == restored["mines"]
    assert 35 not in restored["mines"]
    assert "mines" not in grid.public_view(restored)


def test_callback_64_bytes_and_panel_4096_bytes_under_long_names():
    state = grid.new_memory(chat_id=-(2**63 - 1), thread_id=2**63 - 1,
        owner_id=2**63 - 1, owner_name="<&>" * 20, round_id="f" * 12, now=1)
    for uid in range(2, 5):
        state = grid.memory_join(state, user_id=uid, user_name="<&>" * 20, now=2)
    state["phase_seq"] = 63
    state["turn_seq"] = 63
    assert len(render(state).encode("utf-8")) <= 4096
    for action in ("pick", "open", "cancel"):
        assert len(callback_data(state, action, 35).encode("utf-8")) <= 64
    mines = grid.new_mines(chat_id=-100, thread_id=None, owner_id=1,
                           owner_name="Bir", round_id="r", now=1)
    assert callback_data(mines, "open", 35).endswith(":z")
    assert len(keyboard(mines).inline_keyboard) <= 8


def test_hidden_board_layout_cannot_be_inferred_from_group_text_or_buttons():
    cards = list(grid.CARD_SYMBOLS * 2)
    first = grid.new_memory(chat_id=-100, thread_id=None, owner_id=1,
        owner_name="Bir", round_id="r", now=1, cards=cards)
    second = grid.new_memory(chat_id=-100, thread_id=None, owner_id=1,
        owner_name="Bir", round_id="r", now=1, cards=list(reversed(cards)))
    for state in (first, second):
        state.update(phase="turn", phase_seq=2, phase_ready=True,
                     deadline=100, panel_wait_until=None)
    assert "cards" not in grid.public_view(first)
    assert grid.public_view(first) == grid.public_view(second)
    assert render(first, now=10) == render(second, now=10)
    assert keyboard(first) == keyboard(second)
    mines_a = grid.new_mines(chat_id=-100, thread_id=None, owner_id=1,
        owner_name="Bir", round_id="r", now=1, seed=1)
    mines_b = grid.new_mines(chat_id=-100, thread_id=None, owner_id=1,
        owner_name="Bir", round_id="r", now=1, seed=999)
    assert "seed" not in grid.public_view(mines_a) and "mines" not in grid.public_view(mines_a)
    assert render(mines_a, now=10) == render(mines_b, now=10)
    assert keyboard(mines_a) == keyboard(mines_b)


@pytest.mark.asyncio
async def test_cross_instance_panel_claim_race_and_new_dirty_state_survive_old_ack():
    repo = Repository()
    first, second = GridPuzzlesService(repo), GridPuzzlesService(repo)
    _, state = await first.open(game_key=grid.MEMORY_KEY, chat_id=-100,
                                user_id=1, user_name="Bir")
    claims = await asyncio.gather(
        first.claim_delivery(game_key=grid.MEMORY_KEY, chat_id=-100, now=10),
        second.claim_delivery(game_key=grid.MEMORY_KEY, chat_id=-100, now=10))
    assert sum(claim is not None for claim in claims) == 1
    claim = next(claim for claim in claims if claim is not None)
    await second.act(game_key=grid.MEMORY_KEY, chat_id=-100, user_id=2,
        user_name="İki", action_id="join:2", action="join",
        round_id=state["round_id"], now=11)
    await first.acknowledge_panel(game_key=grid.MEMORY_KEY, chat_id=-100,
        round_id=state["round_id"], expected_version=claim["version"],
        message_id=55, now=12)
    current = await first.current(game_key=grid.MEMORY_KEY, chat_id=-100)
    assert current["panel_message_id"] is None and current["panel_dirty"]


@pytest.mark.asyncio
async def test_late_panel_ack_immediately_cancels_without_starting_clock():
    repo = Repository()
    service = GridPuzzlesService(repo)
    _, state = await service.open(game_key=grid.MINES_KEY, chat_id=-100,
                                  user_id=1, user_name="Bir")
    claimed = await service.claim_delivery(game_key=grid.MINES_KEY, chat_id=-100, now=10)
    success = await service.acknowledge_panel(game_key=grid.MINES_KEY, chat_id=-100,
        round_id=state["round_id"], expected_version=claimed["version"],
        message_id=77, now=state["panel_wait_until"])
    assert success is False
    closed = await service.current(game_key=grid.MINES_KEY, chat_id=-100)
    assert closed["phase"] == "cancelled" and closed["deadline"] is None
    assert closed["panel_dirty"] and not closed["score_pending"]
    assert closed["panel_message_id"] == 77
