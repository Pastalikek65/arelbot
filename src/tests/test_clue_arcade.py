"""Complete clue arcade matches, delivery gates, content cycles and CAS races."""
import asyncio
import time
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from bson import BSON
from telegram.error import TelegramError

from src.games import clue_arcade as rules
from src.handlers.clue_arcade import ClueArcadeHandler
from src.handlers.puzzle_suite import PuzzleSuite
from src.services.clue_arcade_service import ClueArcadeService
from src.ui import clue_arcade_ui as ui


class Repository:
    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat_id, key):
        return deepcopy(self.rows.get((chat_id, key)))

    async def create_game_state_if_absent(self, chat_id, key, state):
        if (chat_id, key) in self.rows:
            return False
        self.rows[chat_id, key] = BSON.encode(state).decode()
        return True

    async def compare_and_save_game_state(self, chat_id, key, state, *, expected_version):
        previous = self.rows.get((chat_id, key))
        if not previous or previous["version"] != expected_version:
            return False
        assert state["version"] == previous["version"] + 1
        self.rows[chat_id, key] = BSON.encode(state).decode()
        return True


class Ledger:
    def __init__(self):
        self.scores = {}
        self.coins = {}

    async def record_score(self, *, event_id, score, **_):
        self.scores.setdefault(event_id, score)

    async def credit(self, user_id, chat_id, amount, *, event_id, reason):
        self.coins.setdefault(event_id, (user_id, chat_id, amount, reason))


async def current(service, game, chat=-100, topic=7):
    return await service.current(game_key=game, chat_id=chat, thread_id=topic)


async def action(service, game, rid, kind, aid, now, *, user=None, chat=-100,
                 topic=7, **kwargs):
    state = await current(service, game, chat, topic)
    user = (chat if chat > 0 else 1) if user is None else user
    user_name = kwargs.pop("user_name", f"Oyuncu {user}")
    return await service.act(game_key=game, chat_id=chat, thread_id=topic,
        user_id=user, user_name=user_name, action_id=aid, action=kind,
        round_id=rid, phase_seq=state["phase_seq"], now=now, **kwargs)


async def acknowledge(service, game, rid, now, *, chat=-100, topic=7, panel=50):
    claim = await service.claim_panel(game_key=game, chat_id=chat,
                                      thread_id=topic, now=now)
    assert claim is not None
    assert await service.acknowledge_panel(game_key=game, chat_id=chat,
        thread_id=topic, round_id=rid, expected_version=claim["version"],
        message_id=panel, now=now)
    return await current(service, game, chat, topic)


@pytest.mark.asyncio
async def test_five_clues_group_fairness_progression_and_reward_after_restart():
    repository, ledger = Repository(), Ledger()
    service = ClueArcadeService(repository, leaderboard=ledger, economy=ledger)
    _, opened = await service.open(game_key=rules.BES_IPUCU, chat_id=-100,
        thread_id=7, user_id=1, user_name="Bir", now=100, seed=4)
    rid = opened["round_id"]
    await action(service, rules.BES_IPUCU, rid, "join", "join2", 101,
                 user=2, user_name="İki")
    await action(service, rules.BES_IPUCU, rid, "start", "start", 102)
    for turn in range(5):
        moment = 110 + turn * 40
        live = await acknowledge(service, rules.BES_IPUCU, rid, moment)
        assert live["round_no"] == turn + 1
        assert len(rules.public_state(live)["card"]["clues"]) == 1
        assert "answer" not in rules.public_state(live)["card"]
        old_seq = live["phase_seq"]
        await action(service, rules.BES_IPUCU, rid, "pass", f"pass:{turn}", moment + 1)
        still = await current(service, rules.BES_IPUCU)
        assert still["clue_no"] == 1
        await action(service, rules.BES_IPUCU, rid, "text", f"wrong:{turn}",
            moment + 2, user=2, text="yanlış cevap", message_id=51 + turn,
            submitted_at=moment + 2)
        advanced = await current(service, rules.BES_IPUCU)
        assert advanced["clue_no"] == 2 and advanced["deadline"] is None
        with pytest.raises(ValueError, match="ca_stale_phase"):
            await service.act(game_key=rules.BES_IPUCU, chat_id=-100, thread_id=7,
                user_id=1, action_id=f"stale:{turn}", action="pass",
                round_id=rid, phase_seq=old_seq, now=moment + 3)
        live = await acknowledge(service, rules.BES_IPUCU, rid, moment + 4)
        assert len(rules.public_state(live)["card"]["clues"]) == 2
        correct = live["card"]["answer"]
        await action(service, rules.BES_IPUCU, rid, "text", f"correct:{turn}",
            moment + 5, text=correct.upper(), message_id=71 + turn,
            submitted_at=moment + 5)
        revealed = await current(service, rules.BES_IPUCU)
        assert revealed["phase"] == "reveal"
        assert revealed["players"][0]["score"] == 8 * (turn + 1)
        await acknowledge(service, rules.BES_IPUCU, rid, moment + 6)
        await service.advance(game_key=rules.BES_IPUCU, chat_id=-100,
                              thread_id=7, round_id=rid, now=moment + 15)
    ended = await current(service, rules.BES_IPUCU)
    assert ended["phase"] == "finished" and ended["score_pending"]
    restarted = ClueArcadeService(repository, leaderboard=ledger, economy=ledger)
    await restarted.settle_scores(game_key=rules.BES_IPUCU, chat_id=-100, thread_id=7)
    await restarted.settle_scores(game_key=rules.BES_IPUCU, chat_id=-100, thread_id=7)
    assert ledger.scores == {f"{rules.BES_IPUCU}:-100:{rid}:1": 40}
    assert len(ledger.coins) == 1


@pytest.mark.asyncio
async def test_emoji_solo_five_rounds_aliases_and_deck_continues_without_repeats():
    repository = Repository()
    service = ClueArcadeService(repository, leaderboard=Ledger())
    _, opened = await service.open(game_key=rules.EMOJI_BILMECE, chat_id=123,
        user_id=123, user_name="Bir", solo=True, now=100, seed=3)
    rid, seen = opened["round_id"], set()
    for turn in range(5):
        moment = 101 + turn * 20
        live = await acknowledge(service, rules.EMOJI_BILMECE, rid, moment,
                                  chat=123, topic=None, panel=90)
        assert live["card"]["id"] not in seen
        seen.add(live["card"]["id"])
        assert "answer" not in rules.public_state(live)["card"]
        with pytest.raises(ValueError, match="ca_stale_message"):
            await action(service, rules.EMOJI_BILMECE, rid, "text", f"old:{turn}",
                moment + .1, chat=123, topic=None, text="yanlış",
                message_id=90, submitted_at=moment + .1)
        await action(service, rules.EMOJI_BILMECE, rid, "text", f"first:{turn}",
            moment + 1, chat=123, topic=None, text="yanlış",
            message_id=100 + turn * 2, submitted_at=moment + 1)
        live = await current(service, rules.EMOJI_BILMECE, 123, None)
        alias = live["card"]["aliases"][0]
        await action(service, rules.EMOJI_BILMECE, rid, "text", f"second:{turn}",
            moment + 2, chat=123, topic=None, text=alias.upper() + "!!!",
            message_id=101 + turn * 2, submitted_at=moment + 2)
        revealed = await current(service, rules.EMOJI_BILMECE, 123, None)
        assert revealed["phase"] == "reveal"
        assert revealed["players"][0]["score"] == 3 * (turn + 1)
        await acknowledge(service, rules.EMOJI_BILMECE, rid, moment + 3,
                          chat=123, topic=None, panel=90)
        await service.advance(game_key=rules.EMOJI_BILMECE, chat_id=123,
                              round_id=rid, now=moment + 12)
    ended = await current(service, rules.EMOJI_BILMECE, 123, None)
    assert ended["phase"] == "finished"
    await service.settle_scores(game_key=rules.EMOJI_BILMECE, chat_id=123)
    _, replay = await service.open(game_key=rules.EMOJI_BILMECE, chat_id=123,
        user_id=123, user_name="Bir", solo=True, now=300, seed=3)
    assert replay["card"]["id"] not in seen


def test_content_is_distinct_unambiguous_and_normalized():
    assert len(rules.EMOJI) >= 50
    assert len({rules.normalize(row[1]) for row in rules.EMOJI}) == len(rules.EMOJI)
    assert len(rules.CLUES) == len(rules.CATEGORIES)
    assert all(len(row) == 6 for row in rules.CLUES)
    assert rules.normalize("  ÇİÇEK, BAHÇESİ! ") == "cicek bahcesi"
    assert rules.normalize("IĞDIR") == "igdir"
    assert len(ui.callback_data({"game_key": rules.BES_IPUCU, "chat_id": -100123456789,
        "thread_id": 99999, "round_id": "123456789abc", "phase_seq": 99}, "p")) <= 64


def test_emoji_alias_allowlist_and_public_hint_hide_answer():
    examples = {"denizde yüzme": "yüzme", "bisiklet yarışı": "bisiklet yarışması",
                "okul kütüphanesi": "kütüphane", "patates kızartması": "kızarmış patates"}
    for answer, alias in examples.items():
        index = next(i for i, row in enumerate(rules.EMOJI) if row[1] == answer)
        item = rules.card(rules.EMOJI_BILMECE, index)
        assert rules.normalize(alias) in item["aliases"]
        assert len(item["word_lengths"]) == len(answer.split())
    state = rules.new(game_key=rules.EMOJI_BILMECE, chat_id=123,
        thread_id=None, owner_id=123, owner_name="Bir", now=100,
        round_id="r2", solo=True, seed=4)
    public = rules.public_state(state)
    assert "answer" not in public["card"] and "aliases" not in public["card"]
    rendered = ui.render(state)
    assert "Kategori:" in rendered and "Harfler:" in rendered
    assert state["card"]["answer"] not in rendered
    lobby = rules.new(game_key=rules.EMOJI_BILMECE, chat_id=-100,
        thread_id=None, owner_id=1, owner_name="Bir", round_id="r3", now=100)
    assert "Önce Katıl" in ui.render(lobby)


def test_last_clue_timeout_reveals_then_finishes_without_idle_reward():
    state = rules.new(game_key=rules.BES_IPUCU, chat_id=123,
        thread_id=None, owner_id=123, owner_name="Bir", round_id="r1",
        now=100, solo=True, seed=1)
    state.update(round_no=5, clue_no=5, deadline=101, panel_wait_until=None)
    revealed = rules.advance(state, now=102)
    assert revealed["phase"] == "reveal"
    assert revealed["card"]["answer"]
    revealed.update(deadline=110, panel_wait_until=None)
    finished = rules.advance(revealed, now=111)
    assert finished["phase"] == "finished"
    assert finished["score_pending"] is False and finished["winner_ids"] == []
    assert finished["card"] is None and finished["deadline"] is None


class Bot:
    def __init__(self, fail=False):
        self.fail = fail
        self.sends = []
        self.edits = []

    async def send_message(self, **kwargs):
        if self.fail:
            raise TelegramError("temporary delivery failure")
        self.sends.append(kwargs)
        return SimpleNamespace(message_id=400)

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)


@pytest.mark.asyncio
async def test_failed_panel_delivery_keeps_clock_closed_across_service_restart():
    repo = Repository()
    service = ClueArcadeService(repo)
    now = time.time()
    _, opened = await service.open(game_key=rules.EMOJI_BILMECE, chat_id=123,
        user_id=123, user_name="Bir", solo=True, now=now)
    handler = ClueArcadeHandler(service)
    with pytest.raises(TelegramError):
        await handler.publish(Bot(fail=True), game_key=rules.EMOJI_BILMECE, chat_id=123)
    pending = await current(service, rules.EMOJI_BILMECE, 123, None)
    assert pending["deadline"] is None and pending["panel_dirty"]
    restarted = ClueArcadeService(repo)
    claim = await restarted.claim_panel(game_key=rules.EMOJI_BILMECE,
        chat_id=123, now=pending["delivery_retry_at"] + 1)
    assert claim is not None
    assert await restarted.acknowledge_panel(game_key=rules.EMOJI_BILMECE,
        chat_id=123, round_id=opened["round_id"], expected_version=claim["version"],
        message_id=400, now=pending["delivery_retry_at"] + 1)
    live = await current(restarted, rules.EMOJI_BILMECE, 123, None)
    assert live["deadline"] == pytest.approx(pending["delivery_retry_at"] + 1 + rules.EMOJI_SECONDS)


@pytest.mark.asyncio
async def test_simultaneous_guesses_only_first_scores_and_old_panel_is_rejected():
    repo = Repository()
    service = ClueArcadeService(repo)
    now = time.time()
    _, opened = await service.open(game_key=rules.EMOJI_BILMECE, chat_id=-100,
        thread_id=7, user_id=1, user_name="Bir", now=now)
    rid = opened["round_id"]
    await action(service, rules.EMOJI_BILMECE, rid, "join", "join:2", now + .1, user=2)
    await action(service, rules.EMOJI_BILMECE, rid, "start", "start", now + .2)
    live = await acknowledge(service, rules.EMOJI_BILMECE, rid, now + .3)
    seq = live["phase_seq"]
    answer = live["card"]["answer"]
    async def submit(uid, message_id):
        try:
            return await service.act(game_key=rules.EMOJI_BILMECE,
                chat_id=-100, thread_id=7, user_id=uid, user_name=f"Oyuncu {uid}",
                action="text", action_id=f"race:{uid}", round_id=rid,
                phase_seq=seq, text=answer, message_id=message_id,
                submitted_at=now + .5, now=now + .5)
        except ValueError as exc:
            return str(exc)
    outcomes = await asyncio.gather(submit(1, 501), submit(2, 502))
    state = await current(service, rules.EMOJI_BILMECE)
    assert state["phase"] == "reveal"
    assert sum(p["score"] for p in state["players"]) == 6
    assert "ca_stale_phase" in outcomes
    query_answers = []
    async def answer_query(*args, **kwargs):
        query_answers.append((args, kwargs))
    query = SimpleNamespace(data=ui.callback_data(live, "l"), id="old",
                            message=SimpleNamespace(message_id=50), answer=answer_query)
    update = SimpleNamespace(callback_query=query,
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_user=SimpleNamespace(id=1, first_name="Bir"))
    assert await ClueArcadeHandler(service).callback(update, SimpleNamespace(bot=Bot()))
    assert query_answers and query_answers[0][1].get("show_alert")


@pytest.mark.asyncio
async def test_suite_routes_metadata_only_then_handler_loads_live_state():
    class RouteRepository(Repository):
        async def list_active_puzzle_routes(self, chat_id, thread_id, user_id):
            live = await self.get_game_state(chat_id, rules.EMOJI_BILMECE)
            return [{"game_type": rules.EMOJI_BILMECE,
                     "state": {"game_key": rules.EMOJI_BILMECE,
                               "phase": live["phase"],
                               "panel_message_id": live["panel_message_id"],
                               "owner_id": live["owner_id"],
                               "players": [{"id": user_id, "active": True}]}}]

    repo = RouteRepository()
    service = ClueArcadeService(repo)
    now = time.time()
    _, opened = await service.open(game_key=rules.EMOJI_BILMECE, chat_id=-100,
        user_id=1, user_name="Bir", now=now)
    rid = opened["round_id"]
    await action(service, rules.EMOJI_BILMECE, rid, "start", "start", now + .1,
                 topic=None)
    handler = ClueArcadeHandler(service)
    suite = PuzzleSuite({rules.EMOJI_BILMECE: handler}, repository=repo)
    message = SimpleNamespace(message_id=500, text="yanlış", reply_to_message=None,
                              date=datetime.fromtimestamp(now + .2, timezone.utc),
                              is_topic_message=False)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_user=SimpleNamespace(id=1, first_name="Bir"),
        effective_message=message, edited_message=None)
    context = SimpleNamespace(bot=Bot())
    metadata = (await repo.list_active_puzzle_routes(-100, None, 1))[0]["state"]
    assert "deadline" not in metadata and handler.accepts_text(metadata)
    assert not await suite.on_text(update, context, "yanlış")
    live = await acknowledge(service, rules.EMOJI_BILMECE, rid, now + .3,
                              topic=None, panel=400)
    message.date = datetime.fromtimestamp(now + .4, timezone.utc)
    assert await suite.on_text(update, context, "yanlış")
    after = await current(service, rules.EMOJI_BILMECE, -100, None)
    assert after["attempts"]["1"] == 1 and live["deadline"] is not None


@pytest.mark.asyncio
async def test_telegram_second_resolution_uses_panel_message_order():
    service = ClueArcadeService(Repository())
    _, opened = await service.open(game_key=rules.EMOJI_BILMECE,
        chat_id=123, user_id=123, user_name="Bir", solo=True, now=98)
    live = await acknowledge(service, rules.EMOJI_BILMECE, opened["round_id"],
        100.8, chat=123, topic=None, panel=50)
    with pytest.raises(ValueError, match="ca_stale_message"):
        await action(service, rules.EMOJI_BILMECE, opened["round_id"],
            "text", "previous-second", 100.9, chat=123, topic=None,
            message_id=51, submitted_at=99, text=live["card"]["answer"])
    with pytest.raises(ValueError, match="ca_stale_message"):
        await action(service, rules.EMOJI_BILMECE, opened["round_id"],
            "text", "panel-message", 100.9, chat=123, topic=None,
            message_id=50, submitted_at=100, text=live["card"]["answer"])
    status, revealed = await action(service, rules.EMOJI_BILMECE,
        opened["round_id"], "text", "after-panel-same-second", 100.95,
        chat=123, topic=None, message_id=51, submitted_at=100,
        text=live["card"]["answer"])
    assert status == "applied" and revealed["phase"] == "reveal"
    assert revealed["players"][0]["score"] == 6


@pytest.mark.asyncio
async def test_group_guesser_must_join_and_rejoining_does_not_reset_attempts():
    service = ClueArcadeService(Repository())
    _, opened = await service.open(game_key=rules.EMOJI_BILMECE,
        chat_id=-100, thread_id=7, user_id=1, user_name="Bir", now=100)
    rid = opened["round_id"]
    await action(service, rules.EMOJI_BILMECE, rid, "start", "start", 101)
    live = await acknowledge(service, rules.EMOJI_BILMECE, rid, 102)
    with pytest.raises(ValueError, match="ca_not_player"):
        await action(service, rules.EMOJI_BILMECE, rid, "text", "outsider", 103,
            user=2, text=live['card']['answer'], message_id=51, submitted_at=103)
    await action(service, rules.EMOJI_BILMECE, rid, "join", "join", 104, user=2)
    await action(service, rules.EMOJI_BILMECE, rid, "text", "wrong", 105,
        user=2, text="yanlış cevap", message_id=52, submitted_at=105)
    await action(service, rules.EMOJI_BILMECE, rid, "leave", "leave", 106, user=2)
    await action(service, rules.EMOJI_BILMECE, rid, "join", "rejoin", 107, user=2)
    assert (await current(service, rules.EMOJI_BILMECE))["attempts"]["2"] == 1
