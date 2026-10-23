"""Complete, deterministic service playthroughs and delivery boundaries."""
from copy import deepcopy

import pytest

from src.games import social_arcade as rules
from src.services import social_arcade_ui as ui
from src.services.social_arcade_service import SocialArcadeService


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


async def current(svc, key):
    return await svc.current(game_key=key, chat_id=-100, thread_id=8)


async def act(svc, key, state, user, action, *, value=None, text=None, t=100, private=False):
    return await svc.act(game_key=key, chat_id=-100, thread_id=8, user_id=user,
        user_name=f"Oyuncu {user}", action_id=f"{state['phase_seq']}:{state['version']}:{user}:{action}",
        action=action, value=value, text=text, round_id=state["round_id"],
        phase_seq=state["phase_seq"], now=t,
        private_message_id=state.get("private_panels", {}).get(str(user), {}).get("message_id") if private else None)


async def delivered(svc, key, *, t=100):
    """Simulate real group/DM receipts; group actionable panel gets its own receipt."""
    for _ in range(9):
        state = await current(svc, key)
        if state["phase"] in rules.TERMINAL or state.get("phase_ready"):
            return state
        claim = await svc.claim_delivery(game_key=key, chat_id=-100, thread_id=8,
                                         kind="panel", now=t)
        if claim:
            await svc.acknowledge_panel(game_key=key, chat_id=-100, thread_id=8,
                round_id=claim["round_id"], expected_version=claim["version"],
                message_id=50, now=t)
        state = await current(svc, key)
        for uid in state.get("private_recipients", []):
            claimed = await svc.claim_delivery(game_key=key, chat_id=-100, thread_id=8,
                                               kind="private", user_id=uid, now=t)
            if claimed:
                await svc.acknowledge_private(game_key=key, chat_id=-100, thread_id=8,
                    round_id=claimed["round_id"], phase_seq=claimed["phase_seq"],
                    user_id=uid, message_id=1000 + uid + claimed["phase_seq"] * 100,
                    expected_version=claimed["version"], now=t)
    raise AssertionError((key, await current(svc, key)))


async def setup(key, count):
    repo, ledger = Repository(), Ledger()
    svc = SocialArcadeService(repo, leaderboard=ledger)
    _, state = await svc.open(game_key=key, chat_id=-100, thread_id=8,
                              user_id=1, user_name="Kurucu", now=100)
    for uid in range(2, count + 1):
        await act(svc, key, await current(svc, key), uid, "join", t=100)
    for uid in range(1, count + 1):
        state = await current(svc, key)
        payload = await svc.mint_invite(game_key=key, chat_id=-100, thread_id=8,
            user_id=uid, round_id=state["round_id"], now=100)
        await svc.accept_invite(payload, user_id=uid, now=101)
    state = await current(svc, key)
    await act(svc, key, state, 1, "start", t=102)
    return svc, repo, ledger


@pytest.mark.asyncio
async def test_battleship_full_win_and_no_secret_public_board():
    key = "amiral_batti"
    svc, _, ledger = await setup(key, 2)
    state = await delivered(svc, key, t=103)
    assert state["deadline"] == 163
    assert "ships" not in rules.public_view(state)
    cells = [cell for ship in state["ships"]["2"] for cell in ship]
    for turn, cell in enumerate(cells):
        state = await current(svc, key)
        await act(svc, key, state, 1, "shot", value=cell, t=104 + turn)
        if turn < len(cells) - 1:
            await delivered(svc, key, t=104 + turn)
    state = await current(svc, key)
    assert state["phase"] == "finished" and state["winner_ids"] == [1]
    await svc.settle_scores(game_key=key, chat_id=-100, thread_id=8)
    await svc.settle_scores(game_key=key, chat_id=-100, thread_id=8)
    assert len(ledger.events) == 2


@pytest.mark.asyncio
async def test_three_player_lie_full_six_narrators_and_receipt_before_clock():
    key = "iki_dogru_bir_yalan"
    svc, _, ledger = await setup(key, 3)
    for index in range(6):
        state = await delivered(svc, key, t=110 + index * 10)
        author = state["author_order"][state["author_index"]]
        for claim in ("Bugün çay içtim", "Bisiklet kullanırım", "Bir köpeğim var"):
            state = await current(svc, key)
            await act(svc, key, state, author, "claim", text=claim, t=111 + index * 10,
                      private=True)
            # Each edited author card is redelivered before the next input.
            await delivered(svc, key, t=111 + index * 10)
        state = await current(svc, key)
        await act(svc, key, state, author, "lie", value=1, t=112 + index * 10,
                  private=True)
        state = await delivered(svc, key, t=113 + index * 10)
        assert state["phase"] == "vote" and state["deadline"] == 173 + index * 10
        assert "lie_index" not in rules.public_view(state)
        for uid in range(1, 4):
            if uid != author:
                await act(svc, key, await current(svc, key), uid, "vote", value=1,
                          t=114 + index * 10, private=True)
        state = await delivered(svc, key, t=115 + index * 10)
        assert state["phase"] == "result"
        await svc.advance(game_key=key, chat_id=-100, thread_id=8,
                          round_id=state["round_id"], now=121 + index * 10)
    state = await current(svc, key)
    assert state["phase"] == "finished"
    await svc.settle_scores(game_key=key, chat_id=-100, thread_id=8)
    assert len(ledger.events) == 3


@pytest.mark.asyncio
async def test_identity_full_three_subjects_and_private_secrecy():
    key = "kimim_ben"
    svc, _, _ = await setup(key, 3)
    for index in range(3):
        state = await delivered(svc, key, t=110 + index * 10)
        subject = state["players"][state["subject_index"]]["id"]
        assert "identities" not in rules.public_view(state)
        await act(svc, key, state, subject, "guess",
                  text=state["identities"][str(subject)], t=111 + index * 10,
                  private=True)
        state = await delivered(svc, key, t=112 + index * 10)
        await svc.advance(game_key=key, chat_id=-100, thread_id=8,
                          round_id=state["round_id"], now=118 + index * 10)
    assert (await current(svc, key))["phase"] == "finished"


def test_identity_catalog_is_varied_and_hint_never_reveals_secret():
    assert len(rules.IDENTITIES) >= 100
    assert len(rules.IDENTITIES) == len(set(rules.IDENTITIES))
    assert set(rules.IDENTITY_CATEGORY.values()) == {"Meslek", "Hayvan", "Eşya", "Yer"}
    state = rules.new(game_key="kimim_ben", chat_id=-100, thread_id=8,
                      owner_id=1, owner_name="Kurucu", round_id="test", now=100)
    for uid in (2, 3):
        state = rules.join(state, user_id=uid, user_name=f"Oyuncu {uid}", now=100)
    for uid in (1, 2, 3):
        state = rules.ready(state, user_id=uid)
    state = rules.start(state, user_id=1, now=101, seed=2)
    subject = state["players"][0]["id"]
    secret = state["identities"][str(subject)]
    state["question_count"] = 2
    public = ui.group_text(state)
    private = ui.private_text(state, subject)
    assert f"kategori: {rules.IDENTITY_CATEGORY[secret]}" in public
    assert f"kategori: {rules.IDENTITY_CATEGORY[secret]}" in private
    assert secret not in public and secret not in private


@pytest.mark.asyncio
async def test_team_trivia_full_equal_ten_turns():
    key = "takim_bilgi_ligi"
    svc, _, _ = await setup(key, 4)
    for turn in range(10):
        state = await delivered(svc, key, t=110 + turn * 10)
        assert state["phase"] == "trivia"
        members = [p["id"] for p in state["players"] if state["teams"][str(p["id"])] == turn % 2]
        captain = members[(turn // 2) % len(members)]
        await act(svc, key, state, captain, "answer", value=state["questions"][turn][2],
                  t=111 + turn * 10)
        state = await delivered(svc, key, t=112 + turn * 10)
        await svc.advance(game_key=key, chat_id=-100, thread_id=8,
                          round_id=state["round_id"], now=118 + turn * 10)
    state = await current(svc, key)
    assert state["phase"] == "finished" and state["team_scores"] == [5, 5]


@pytest.mark.asyncio
async def test_poison_full_five_rounds_and_hidden_role():
    key = "zehirli_kadeh"
    svc, _, _ = await setup(key, 3)
    for index in range(5):
        state = await delivered(svc, key, t=110 + index * 10)
        assert "poisoner_id" not in rules.public_view(state)
        poisoner = state["poisoner_id"]
        await act(svc, key, state, poisoner, "poison", value=0,
                  t=111 + index * 10, private=True)
        state = await delivered(svc, key, t=112 + index * 10)
        server, target = rules.poison_server_and_target(state)
        await act(svc, key, state, server, "serve", value=1,
                  t=113 + index * 10, private=True)
        state = await delivered(svc, key, t=114 + index * 10)
        await act(svc, key, state, target, "drink", t=115 + index * 10, private=True)
        state = await delivered(svc, key, t=116 + index * 10)
        await svc.advance(game_key=key, chat_id=-100, thread_id=8,
                          round_id=state["round_id"], now=122 + index * 10)
    assert (await current(svc, key))["phase"] == "finished"
