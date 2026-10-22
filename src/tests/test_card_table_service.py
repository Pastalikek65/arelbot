"""Delivery, CAS, secrecy and complete-match checks for the card table."""
from copy import deepcopy
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.games import card_table as rules
from src.services.card_table_service import CardTableService
from src.ui import card_table_ui as ui
from src.handlers.card_table import CardTableHandler


class Repository:
    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat_id, key):
        return deepcopy(self.rows.get((chat_id, key)))

    async def create_game_state_if_absent(self, chat_id, key, state):
        if (chat_id, key) in self.rows:
            return False
        self.rows[(chat_id, key)] = deepcopy(state)
        return True

    async def compare_and_save_game_state(self, chat_id, key, state, *, expected_version):
        old = self.rows.get((chat_id, key))
        if old is None or old["version"] != expected_version:
            return False
        self.rows[(chat_id, key)] = deepcopy(state)
        return True


class Ledger:
    def __init__(self):
        self.events = set()

    async def record_score(self, *, event_id, **kwargs):
        self.events.add(event_id)


async def current(service, key):
    return await service.current(game_key=key, chat_id=-100, thread_id=8)


async def action(service, key, state, uid, code, *, value=None, now=100, private=False, rng=None):
    return await service.act(game_key=key, chat_id=-100, thread_id=8,
        user_id=uid, action_id=f"test:{state['round_id']}:{state['phase_seq']}:{uid}:{code}:{state['version']}",
        action=code, value=value, round_id=state["round_id"],
        phase_seq=state["phase_seq"], now=now, rng=rng,
        private_message_id=(state.get("private_panels", {}).get(str(uid), {}).get("message_id")
                            if private else None))


async def receipts(service, key, *, now=100):
    """Model actual Telegram send/edit receipts, including final clock-panel repair."""
    for _ in range(10):
        state = await current(service, key)
        if state["phase"] != "playing" or state["phase_ready"]:
            return state
        claim = await service.claim_delivery(game_key=key, chat_id=-100, thread_id=8,
                                              kind="panel", now=now)
        if claim:
            await service.acknowledge_panel(game_key=key, chat_id=-100, thread_id=8,
                round_id=claim["round_id"], expected_version=claim["version"],
                message_id=50, now=now)
        state = await current(service, key)
        for uid in state["private_recipients"]:
            claim = await service.claim_delivery(game_key=key, chat_id=-100,
                thread_id=8, kind="private", user_id=uid, now=now)
            if claim:
                await service.acknowledge_private(game_key=key, chat_id=-100,
                    thread_id=8, round_id=claim["round_id"],
                    phase_seq=claim["phase_seq"], user_id=uid,
                    expected_version=claim["version"],
                    message_id=1000 + uid + claim["phase_seq"] * 100, now=now)
    raise AssertionError("private/group delivery never completed")


async def setup(key, count, *, seed=None):
    repo, ledger = Repository(), Ledger()
    service = CardTableService(repo, leaderboard=ledger)
    _, state = await service.open(game_key=key, chat_id=-100, thread_id=8,
                                  user_id=1, user_name="Owner", now=100)
    for uid in range(2, count + 1):
        await action(service, key, await current(service, key), uid, "join")
    for uid in range(1, count + 1):
        state = await current(service, key)
        payload = await service.mint_invite(game_key=key, chat_id=-100, thread_id=8,
            user_id=uid, round_id=state["round_id"], now=100)
        assert len(payload.encode()) <= 64
        await service.accept_invite(payload, user_id=uid, now=101)
    state = await current(service, key)
    if seed is None:
        await action(service, key, state, 1, "start", now=102)
    else:
        # The service starts through rules.act(), which chooses a random seed.
        # Pin only that draw so the complete-match path is repeatable.
        with patch.object(rules.secrets, "randbits", return_value=seed):
            await action(service, key, state, 1, "start", now=102)
    return repo, ledger, service


@pytest.mark.asyncio
@pytest.mark.parametrize("key,count", [("pisti", 2), ("pisti", 4), ("papaz_kimde", 3), ("papaz_kimde", 8)])
async def test_full_matches_with_every_hand_receipt_and_once_only_rewards(key, count):
    repo, ledger, service = await setup(key, count, seed=7)
    second = CardTableService(repo, leaderboard=ledger)
    rng = random.Random(7)
    for step in range(300):
        state = await current(service, key)
        if state["phase"] == "finished":
            break
        state = await receipts(second, key, now=103 + step)
        uid = state["players"][state["turn_index"]]["id"]
        await action(service, key, state, uid,
                     "play" if key == "pisti" else "draw",
                     value=0 if key == "pisti" else None,
                     now=103 + step, private=True, rng=rng)
    else:
        pytest.fail("match did not terminate")
    assert state["phase"] == "finished"
    assert state["deadline"] is None and state["panel_wait_until"] is None
    assert state["winner_ids"]
    assert state["score_pending"]
    await second.settle_scores(game_key=key, chat_id=-100, thread_id=8)
    before = ledger.events.copy()
    assert len(before) == count
    await service.settle_scores(game_key=key, chat_id=-100, thread_id=8)
    assert ledger.events == before
    _, fresh = await service.open(game_key=key, chat_id=-100, thread_id=8,
                                  user_id=1, user_name="New", now=2000)
    assert fresh["phase"] == "lobby" and fresh["round_id"] != state["round_id"]


@pytest.mark.asyncio
async def test_oldmaid_turn_cap_cancels_without_reward_events():
    _, ledger, service = await setup("papaz_kimde", 8, seed=1)
    rng = random.Random(7)
    for step in range(240):
        state = await receipts(service, "papaz_kimde", now=103 + step)
        uid = state["players"][state["turn_index"]]["id"]
        await action(service, "papaz_kimde", state, uid, "draw",
                     now=103 + step, private=True, rng=rng)
    cancelled = await current(service, "papaz_kimde")
    assert cancelled["phase"] == "cancelled"
    assert cancelled["reason"] == "turn_cap"
    assert cancelled["draw_count"] == 240
    assert cancelled["deadline"] is None and cancelled["panel_wait_until"] is None
    assert not cancelled["score_pending"] and not cancelled["winner_ids"]
    await service.settle_scores(game_key="papaz_kimde", chat_id=-100, thread_id=8)
    assert not ledger.events


@pytest.mark.asyncio
async def test_pisti_draw_saves_both_team_scores_without_winner_coins():
    repo = Repository()
    leaderboard = SimpleNamespace(record_score=AsyncMock())
    economy = SimpleNamespace(credit=AsyncMock())
    service = CardTableService(repo, leaderboard=leaderboard, economy=economy)
    state = rules.start(_ready_lobby(), user_id=1, now=101, seed=8)
    for turn in range(48):
        state["phase_ready"], state["deadline"] = True, 1000
        actor = state["players"][state["turn_index"]]["id"]
        state = rules.act(state, user_id=actor, action="play", value=0, now=200 + turn)
    repo.rows[(-100, service.key("pisti", 8))] = deepcopy(state)
    text = ui.group_text(state)
    assert "Berabere" in text
    assert "as/vale" in text and "çok kart" in text

    await service.settle_scores(game_key="pisti", chat_id=-100, thread_id=8)
    assert leaderboard.record_score.await_count == 2
    assert {call.kwargs["score"] for call in leaderboard.record_score.await_args_list} == {8}
    economy.credit.assert_not_awaited()
    assert (await current(service, "pisti"))["score_pending"] is False
    await service.settle_scores(game_key="pisti", chat_id=-100, thread_id=8)
    assert leaderboard.record_score.await_count == 2


@pytest.mark.asyncio
async def test_clock_waits_for_all_private_hands_and_stale_panel_repairs():
    _, _, service = await setup("pisti", 2)
    state = await current(service, "pisti")
    assert state["deadline"] is None
    claim = await service.claim_delivery(game_key="pisti", chat_id=-100,
        thread_id=8, kind="panel", now=103)
    assert await service.acknowledge_panel(game_key="pisti", chat_id=-100,
        thread_id=8, round_id=claim["round_id"], expected_version=claim["version"],
        message_id=50, now=103)
    claim = await service.claim_delivery(game_key="pisti", chat_id=-100,
        thread_id=8, kind="private", user_id=1, now=103)
    await service.acknowledge_private(game_key="pisti", chat_id=-100, thread_id=8,
        round_id=claim["round_id"], phase_seq=claim["phase_seq"], user_id=1,
        expected_version=claim["version"], message_id=1101, now=103)
    state = await current(service, "pisti")
    assert state["deadline"] is None and not state["phase_ready"]
    assert not await service.acknowledge_panel(game_key="pisti", chat_id=-100,
        thread_id=8, round_id=state["round_id"], expected_version=claim["version"],
        message_id=99, now=103)
    state = await current(service, "pisti")
    assert state["panel_message_id"] == 50 and state["panel_dirty"]
    claim = await service.claim_delivery(game_key="pisti", chat_id=-100,
        thread_id=8, kind="private", user_id=2, now=103)
    await service.acknowledge_private(game_key="pisti", chat_id=-100, thread_id=8,
        round_id=claim["round_id"], phase_seq=claim["phase_seq"], user_id=2,
        expected_version=claim["version"], message_id=1102, now=105)
    state = await current(service, "pisti")
    assert state["phase_ready"] and state["deadline"] == 165


@pytest.mark.asyncio
async def test_two_instances_duplicate_turn_late_action_and_private_bind():
    repo, _, service = await setup("papaz_kimde", 3)
    second = CardTableService(repo)
    state = await receipts(service, "papaz_kimde", now=105)
    actor = state["players"][state["turn_index"]]["id"]
    kwargs = dict(game_key="papaz_kimde", chat_id=-100, thread_id=8,
                  user_id=actor, action_id="same-draw", action="draw",
                  round_id=state["round_id"], phase_seq=state["phase_seq"], now=106,
                  private_message_id=state["private_panels"][str(actor)]["message_id"])
    assert (await service.act(**kwargs))[0] == "applied"
    with pytest.raises(ValueError, match="ct_stale_phase"):
        await second.act(**kwargs)
    current_state = await current(service, "papaz_kimde")
    assert current_state["turn_no"] == 1
    with pytest.raises(ValueError, match="ct_stale_hand"):
        await second.act(**{**kwargs, "action_id": "late", "phase_seq": current_state["phase_seq"],
                            "now": 200})


@pytest.mark.asyncio
async def test_inline_expected_version_cas_rejects_stale_choice():
    _, _, service = await setup("pisti", 2)
    state = await receipts(service, "pisti", now=105)
    actor = state["players"][state["turn_index"]]["id"]
    kwargs = dict(game_key="pisti", chat_id=-100, thread_id=8,
                  user_id=actor, action_id="inline:play-0", action="play", value=0,
                  round_id=state["round_id"], phase_seq=state["phase_seq"], now=106,
                  private_message_id=state["private_panels"][str(actor)]["message_id"])
    status, ignored = await service.act(**kwargs, expected_version=state["version"] - 1)
    assert status == "ignored" and ignored["turn_no"] == 0
    assert (await current(service, "pisti"))["version"] == state["version"]
    status, applied = await service.act(**kwargs, expected_version=state["version"])
    assert status == "applied" and applied["turn_no"] == 1


@pytest.mark.asyncio
async def test_blocked_private_hand_cancels_without_award_or_clock():
    _, ledger, service = await setup("pisti", 2)
    initial = await current(service, "pisti")
    _, no_change = await service.advance(game_key="pisti", chat_id=-100,
        thread_id=8, round_id=initial["round_id"], now=initial["panel_wait_until"] - 1)
    assert no_change["deadline"] is None
    _, cancelled = await service.advance(game_key="pisti", chat_id=-100,
        thread_id=8, round_id=initial["round_id"], now=initial["panel_wait_until"])
    assert cancelled["phase"] == "cancelled"
    assert cancelled["deadline"] is None and cancelled["panel_wait_until"] is None
    assert not cancelled["score_pending"] and not cancelled["winner_ids"]
    await service.settle_scores(game_key="pisti", chat_id=-100, thread_id=8)
    assert not ledger.events


@pytest.mark.asyncio
@pytest.mark.parametrize("key,count", [("pisti", 2), ("papaz_kimde", 3)])
async def test_immediate_player_forfeit_does_not_create_reward_events(key, count):
    _, ledger, service = await setup(key, count)
    state = await current(service, key)
    loser = state["players"][1]["id"]
    _, ended = await action(service, key, state, loser, "leave", now=103)
    assert ended["phase"] == "finished" and ended["winner_ids"]
    assert not ended["score_pending"]
    assert all(score == 0 for score in ended["scores"].values())
    await service.settle_scores(game_key=key, chat_id=-100, thread_id=8)
    assert not ledger.events


@pytest.mark.asyncio
async def test_stale_clock_does_not_expire_new_match_after_restart():
    repo, _, service = await setup("pisti", 2)
    initial = await current(service, "pisti")
    await service.advance(game_key="pisti", chat_id=-100, thread_id=8,
        round_id=initial["round_id"], now=initial["panel_wait_until"])
    _, fresh = await service.open(game_key="pisti", chat_id=-100, thread_id=8,
                                   user_id=5, user_name="New", now=500)
    second = CardTableService(repo)
    with pytest.raises(ValueError, match="ct_stale_round"):
        await second.advance(game_key="pisti", chat_id=-100, thread_id=8,
            round_id=initial["round_id"], now=1000)
    assert (await current(service, "pisti"))["round_id"] == fresh["round_id"]


@pytest.mark.asyncio
async def test_duplicate_private_delivery_ack_cannot_extend_timer():
    _, _, service = await setup("pisti", 2)
    state = await receipts(service, "pisti", now=105)
    deadline = state["deadline"]
    uid = state["players"][state["turn_index"]]["id"]
    receipt = state["private_panels"][str(uid)]
    status, _ = await service.acknowledge_private(game_key="pisti", chat_id=-100,
        thread_id=8, round_id=state["round_id"], phase_seq=state["phase_seq"],
        user_id=uid, expected_version=state["version"],
        message_id=receipt["message_id"], now=120)
    assert status in {"ignored", "duplicate"}
    assert (await current(service, "pisti"))["deadline"] == deadline


@pytest.mark.asyncio
async def test_actor_cannot_play_after_deadline_before_worker_tick():
    _, _, service = await setup("pisti", 2)
    state = await receipts(service, "pisti", now=105)
    uid = state["players"][state["turn_index"]]["id"]
    with pytest.raises(ValueError, match="ct_wrong_phase"):
        await action(service, "pisti", state, uid, "play", value=0,
                     now=state["deadline"] + .001, private=True)
    assert (await current(service, "pisti"))["turn_no"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("key,count", [("pisti", 2), ("papaz_kimde", 3)])
async def test_transport_delivers_protected_private_hands_and_arms_timer(monkeypatch, key, count):
    _, _, service = await setup(key, count)
    monkeypatch.setattr("src.services.card_table_service.time.time", lambda: 103.0)

    class Bot:
        def __init__(self):
            self.sent = []
            self.edited = []
            self.next_id = 20

        async def send_message(self, **kwargs):
            self.next_id += 1
            self.sent.append(kwargs)
            return SimpleNamespace(message_id=self.next_id)

        async def edit_message_text(self, **kwargs):
            self.edited.append(kwargs)
            return True

    bot = Bot()
    handler = CardTableHandler(service, inline_service=SimpleNamespace(
        create_query=lambda state: "ct signed-scope"))
    await handler.process(bot, {"state": await current(service, key)})
    state = await current(service, key)
    assert state["phase_ready"] and state["deadline"] == 163
    private = [entry for entry in bot.sent if entry["chat_id"] > 0]
    assert len(private) == count and all(entry.get("protect_content") is True for entry in private)
    assert all("Elin:" in entry["text"] for entry in private)
    actor = state["players"][state["turn_index"]]["id"]
    assert any(button.switch_inline_query_current_chat == "ct signed-scope"
               for entry in private if entry["chat_id"] == actor
               for row in entry["reply_markup"].inline_keyboard for button in row)
    assert not any(button.switch_inline_query_current_chat
                   for entry in private if entry["chat_id"] != actor
                   for row in (entry["reply_markup"].inline_keyboard
                               if entry["reply_markup"] else []) for button in row)
    assert any(entry["chat_id"] == -100 for entry in bot.sent)


def test_public_panel_excludes_hidden_cards_and_callbacks_fit():
    state = rules.start(_ready_lobby(), user_id=1, now=101, seed=3)
    view = rules.public_state(state)
    assert "hands" not in view and "deck" not in view and "captured" not in view
    assert "dm_invites" not in view and "private_panels" not in view
    assert len(ui.callback_data(state, "p0").encode()) <= 64
    assert ui.parse_callback(ui.callback_data(state, "p0"))[-1] == "p0"


def test_oldmaid_initial_private_hand_renders_before_first_draw():
    lobby = rules.new(game_key="papaz_kimde", chat_id=-100, owner_id=1,
                      owner_name="<Owner>&", now=100)
    for uid in (2, 3):
        lobby = rules.join(lobby, user_id=uid, user_name=f"Player {uid}", now=100)
    for uid in (1, 2, 3):
        lobby = rules.ready(lobby, user_id=uid)
    state = rules.start(lobby, user_id=1, now=101, seed=3)
    for uid in (1, 2, 3):
        assert "Elin:" in ui.private_text(state, uid)
    assert "&lt;Owner&gt;&amp;" in ui.group_text(state)


def _ready_lobby():
    state = rules.new(game_key="pisti", chat_id=-100, thread_id=8,
                      owner_id=1, owner_name="Owner", now=100)
    state = rules.join(state, user_id=2, user_name="Two", now=100)
    state = rules.ready(state, user_id=1)
    state = rules.ready(state, user_id=2)
    return state
