"""Word-chain rules and persistence races stay independent of Telegram."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from src.games import word_chain
from src.services.word_chain_service import WordChainService
from src.services.word_chain_ui import render_word_chain
from src.handlers.game_runtime import GameRuntime


class Repository:
    def __init__(self):
        self.rows = {}
        self.lock = asyncio.Lock()

    async def get_game_state(self, chat_id, game_type):
        async with self.lock:
            row = self.rows.get((chat_id, game_type))
            return deepcopy(row) if row else None

    async def create_game_state_if_absent(self, chat_id, game_type, state):
        async with self.lock:
            key = (chat_id, game_type)
            if key in self.rows:
                return False
            self.rows[key] = deepcopy(state)
            return True

    async def compare_and_save_game_state(self, chat_id, game_type, state, *, expected_version):
        async with self.lock:
            key = (chat_id, game_type)
            current = self.rows.get(key)
            if current is None or current["version"] != expected_version:
                return False
            assert state["version"] == expected_version + 1
            self.rows[key] = deepcopy(state)
            return True


def test_rules_enforce_turn_letter_dictionary_and_repetition():
    lobby = word_chain.new_lobby(chat_id=-100, thread_id=7, owner_id=1, owner_name="İlk")
    lobby = word_chain.join(lobby, user_id=2, user_name="İkinci")
    state = word_chain.start(lobby, user_id=1, now=10)
    with pytest.raises(ValueError, match="not_your_turn"):
        word_chain.submit(state, user_id=2, text="masa", now=11)
    state = word_chain.submit(state, user_id=1, text="masa", now=11)
    assert state["last_word"] == "masa"
    assert state["turn_index"] == 1
    with pytest.raises(ValueError, match="wrong_letter"):
        word_chain.submit(state, user_id=2, text="kedi", now=12)
    state = word_chain.submit(state, user_id=2, text="aslan", now=12)
    assert state["scores"] == {"1": 4, "2": 5}
    with pytest.raises(ValueError, match="repeated_word"):
        word_chain.submit(state, user_id=1, text="aslan", now=13)
    with pytest.raises(ValueError, match="unknown_word"):
        word_chain.submit(state, user_id=1, text="nxyz", now=13)
    assert lobby["phase"] == "lobby"
    assert "İlk" in render_word_chain(state)


def test_expired_turn_cannot_score_and_only_owner_can_stop():
    lobby = word_chain.new_lobby(chat_id=-100, thread_id=None, owner_id=1, owner_name="A")
    state = word_chain.start(word_chain.join(lobby, user_id=2, user_name="B"), user_id=1, now=1)
    with pytest.raises(ValueError, match="turn_expired"):
        word_chain.submit(state, user_id=1, text="masa", now=41)
    with pytest.raises(ValueError, match="not_owner"):
        word_chain.stop(state, user_id=2)
    assert word_chain.stop(state, user_id=2, moderator=True)["phase"] == "cancelled"


@pytest.mark.asyncio
async def test_service_is_durable_idempotent_and_topic_scoped():
    repository = Repository()
    first = WordChainService(repository)
    status, lobby = await first.open(chat_id=-100, thread_id=5, user_id=1, user_name="A")
    assert status == "created"
    second = WordChainService(repository)
    assert (await second.open(chat_id=-100, thread_id=5, user_id=2, user_name="B"))[0] == "existing"
    assert (await second.open(chat_id=-100, thread_id=6, user_id=2, user_name="B"))[0] == "created"
    status, joined = await first.act(chat_id=-100, thread_id=5, user_id=2, user_name="B", action_id="msg:10", action="join")
    assert status == "join" and len(joined["players"]) == 2
    status, same = await first.act(chat_id=-100, thread_id=5, user_id=2, user_name="B", action_id="msg:10", action="join")
    assert status == "duplicate" and same["version"] == joined["version"]
    started = (await second.act(chat_id=-100, thread_id=5, user_id=1, action_id="msg:11", action="start"))[1]
    assert started["phase"] == "playing"
    assert (await first.current(-100, 5))["phase"] == "playing"
    assert (await first.current(-100, 6))["phase"] == "lobby"


@pytest.mark.asyncio
async def test_expired_turn_is_advanced_once_and_panel_is_acknowledged():
    repository = Repository()
    service = WordChainService(repository)
    await service.open(chat_id=-100, thread_id=9, user_id=1, user_name="A")
    await service.act(chat_id=-100, thread_id=9, user_id=2, user_name="B", action_id="msg:31", action="join")
    await service.act(chat_id=-100, thread_id=9, user_id=1, action_id="msg:32", action="start")
    state = await service.current(-100, 9)
    deadline = float(state["turn_expires_at"])
    assert await service.expire(chat_id=-100, thread_id=9, expected_round_id=state["round_id"], now=deadline - 1) is None
    advanced = await service.expire(chat_id=-100, thread_id=9, expected_round_id=state["round_id"], now=deadline)
    assert advanced["turn_index"] == 1 and advanced["panel_dirty"]
    assert await service.expire(chat_id=-100, thread_id=9, expected_round_id=state["round_id"], now=deadline) is None
    assert await service.acknowledge_panel(chat_id=-100, thread_id=9, round_id=state["round_id"], expected_version=advanced["version"])
    assert not (await service.current(-100, 9))["panel_dirty"]


@pytest.mark.asyncio
async def test_runtime_sends_timeout_notice_to_the_original_topic():
    repository = Repository()
    service = WordChainService(repository)
    await service.open(chat_id=-100, thread_id=9, user_id=1, user_name="A")
    await service.act(chat_id=-100, thread_id=9, user_id=2, user_name="B", action_id="msg:41", action="join")
    await service.act(chat_id=-100, thread_id=9, user_id=1, action_id="msg:42", action="start")
    state = await service.current(-100, 9)
    state["turn_expires_at"] = 1.0
    state["version"] += 1
    assert await repository.compare_and_save_game_state(-100, service._key(9), state, expected_version=state["version"] - 1)

    class Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    bot = Bot()
    await GameRuntime(repository)._process(bot, {"chat_id": -100, "game_type": service._key(9), "state": state})
    assert len(bot.sent) == 1
    assert bot.sent[0]["message_thread_id"] == 9
    assert not (await service.current(-100, 9))["panel_dirty"]


@pytest.mark.asyncio
async def test_simultaneous_join_actions_preserve_both_players():
    repository = Repository()
    one, two = WordChainService(repository), WordChainService(repository)
    await one.open(chat_id=-100, thread_id=None, user_id=1, user_name="A")
    await asyncio.gather(
        one.act(chat_id=-100, thread_id=None, user_id=2, user_name="B", action_id="msg:21", action="join"),
        two.act(chat_id=-100, thread_id=None, user_id=3, user_name="C", action_id="msg:22", action="join"),
    )
    assert len((await one.current(-100, None))["players"]) == 3


def test_turkish_letters_stay_distinct_and_documented_example_is_valid():
    assert word_chain._word("KALEM") == "kalem"
    assert word_chain._word("ARI") == "arı"
    lobby = word_chain.new_lobby(chat_id=-100, thread_id=None, owner_id=1, owner_name="A")
    state = word_chain.start(word_chain.join(lobby, user_id=2, user_name="B"), user_id=1, now=1)
    state = word_chain.submit(state, user_id=1, text="arı", now=2)
    with pytest.raises(ValueError, match="wrong_letter"):
        word_chain.submit(state, user_id=2, text="iplik", now=3)
    assert word_chain.submit(state, user_id=2, text="ırmak", now=3)["last_word"] == "ırmak"


def test_every_player_gets_five_turns_and_passes_are_limited():
    state = word_chain.new_lobby(chat_id=-100, thread_id=None, owner_id=1, owner_name="A")
    for uid in range(2, 21):
        state = word_chain.join(state, user_id=uid, user_name=str(uid))
    state = word_chain.start(state, user_id=1, now=1)
    assert state["max_turns"] == 100
    counts = {uid: 0 for uid in range(1, 21)}
    for _ in range(100):
        counts[state["players"][state["turn_index"]]["id"]] += 1
        state = word_chain.expire_turn(state, now=state["turn_expires_at"])
    assert set(counts.values()) == {5}
    assert state["phase"] == "finished"
    assert state["winner_ids"] == []  # Nobody answered; no free win or reward.


@pytest.mark.asyncio
async def test_lost_database_ack_does_not_repeat_a_committed_action():
    class LostAckRepository(Repository):
        lose_ack = False

        async def compare_and_save_game_state(self, *args, **kwargs):
            saved = await super().compare_and_save_game_state(*args, **kwargs)
            if saved and self.lose_ack:
                self.lose_ack = False
                raise RuntimeError("connection_lost_after_commit")
            return saved

    repository = LostAckRepository()
    service = WordChainService(repository)
    await service.open(chat_id=-100, thread_id=None, user_id=1, user_name="A")
    await service.act(chat_id=-100, thread_id=None, user_id=2, user_name="B", action_id="cmd:10", action="join")
    await service.act(chat_id=-100, thread_id=None, user_id=1, action_id="cmd:11", action="start")
    repository.lose_ack = True
    try:
        await service.act(chat_id=-100, thread_id=None, user_id=1, action_id="cmd:12", action="pass")
    except RuntimeError:
        pass
    before = await service.current(-100, None)
    outcome, after = await service.act(chat_id=-100, thread_id=None, user_id=1, action_id="cmd:12", action="pass")
    assert outcome == "duplicate"
    assert after["turn_count"] == before["turn_count"] == 1


@pytest.mark.asyncio
async def test_stale_turn_and_old_panel_ack_cannot_change_newer_state():
    repository = Repository()
    service = WordChainService(repository)
    await service.open(chat_id=-100, thread_id=None, user_id=1, user_name="A")
    await service.act(chat_id=-100, thread_id=None, user_id=2, user_name="B", action_id="cmd:1", action="join")
    state = (await service.act(chat_id=-100, thread_id=None, user_id=1, action_id="cmd:2", action="start"))[1]
    state = await service.expire(chat_id=-100, thread_id=None, expected_round_id=state["round_id"], now=state["turn_expires_at"])
    old_version = state["version"]
    later = await service.expire(chat_id=-100, thread_id=None, expected_round_id=state["round_id"], now=state["turn_expires_at"])
    with pytest.raises(ValueError, match="stale_turn"):
        await service.act(chat_id=-100, thread_id=None, user_id=1, action_id="cmd:3", action="word", value="masa", expected_round_id=later["round_id"], expected_turn=0)
    assert not await service.acknowledge_panel(chat_id=-100, thread_id=None, round_id=later["round_id"], expected_version=old_version)
    assert (await service.current(-100, None))["panel_dirty"] is True


@pytest.mark.asyncio
async def test_score_and_coin_settlement_retry_survives_partial_delivery():
    class Scores:
        def __init__(self):
            self.events = set()

        async def record_score(self, **kwargs):
            fresh = kwargs["event_id"] not in self.events
            self.events.add(kwargs["event_id"])
            return fresh

    class Coins:
        def __init__(self):
            self.events = set()
            self.fail = True

        async def credit(self, *args, **kwargs):
            if self.fail:
                self.fail = False
                raise RuntimeError("coin_store_offline")
            self.events.add(kwargs["event_id"])

    repository, scores, coins = Repository(), Scores(), Coins()
    service = WordChainService(repository, leaderboard=scores, economy=coins)
    state = word_chain.new_lobby(chat_id=-100, thread_id=None, owner_id=1, owner_name="A")
    state = word_chain.start(word_chain.join(state, user_id=2, user_name="B"), user_id=1, now=1)
    state = word_chain.submit(state, user_id=1, text="masa", now=2)
    state["turn_count"] = state["max_turns"] - 1
    state = word_chain.expire_turn(state, now=state["turn_expires_at"])
    repository.rows[(-100, service._key(None))] = state
    with pytest.raises(RuntimeError, match="coin_store_offline"):
        await service.settle_scores(chat_id=-100, thread_id=None)
    assert (await service.current(-100, None))["score_pending"]
    await service.settle_scores(chat_id=-100, thread_id=None)
    assert len(scores.events) == len(coins.events) == 1
    assert not (await service.current(-100, None))["score_pending"]


def test_lobby_exit_transfers_owner_and_controls_are_round_bound():
    from src.services.word_chain_ui import word_chain_keyboard
    state = word_chain.new_lobby(chat_id=-100, thread_id=None, owner_id=1, owner_name="<A>")
    state = word_chain.join(state, user_id=2, user_name="B")
    left = word_chain.leave(state, user_id=1)
    assert left["owner_id"] == 2
    assert [player["id"] for player in left["players"]] == [2]
    buttons = [button for row in word_chain_keyboard(left).inline_keyboard for button in row]
    assert all(left["round_id"] in button.callback_data for button in buttons)
    assert "&lt;A&gt;" in render_word_chain(state)


def test_panel_uses_remaining_seconds_and_fair_match_length():
    state = word_chain.new_lobby(chat_id=-100, thread_id=None, owner_id=1, owner_name="A")
    state = word_chain.start(word_chain.join(state, user_id=2, user_name="B"), user_id=1, now=100)
    panel = render_word_chain(state, now=125)
    assert "5 saniye" in panel
    assert "/10" in panel


@pytest.mark.asyncio
async def test_real_callback_adapter_starts_game_and_rejects_old_pass_panel():
    from types import SimpleNamespace
    from src.handlers.callback_handler import CallbackHandler
    repository = Repository()
    service = WordChainService(repository)
    _, state = await service.open(chat_id=-100, thread_id=None, user_id=1, user_name="A")
    await service.act(chat_id=-100, thread_id=None, user_id=2, user_name="B", action_id="msg:1", action="join")

    class Query:
        id = "start1"
        message = SimpleNamespace(message_thread_id=None)

        def __init__(self):
            self.edits, self.alerts = [], []

        async def edit_message_text(self, text, **kwargs):
            self.edits.append(text)

        async def answer(self, text="", **kwargs):
            self.alerts.append(text)

    query = Query()
    handler = CallbackHandler.__new__(CallbackHandler)
    handler.word_chain_service = service
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100, type="supergroup"), effective_user=SimpleNamespace(id=1, first_name="A"))
    await handler._handle_word_chain_callback(update, query, None, f"chain:{state['round_id']}:0:start")
    assert (await service.current(-100, None))["phase"] == "playing"
    assert query.alerts == [""]
    from telegram.error import BadRequest
    original_edit = query.edit_message_text
    async def unchanged(*args, **kwargs):
        raise BadRequest("Message is not modified")
    query.edit_message_text = unchanged
    await handler._handle_word_chain_callback(update, query, None, f"chain:{state['round_id']}:0:refresh")
    assert query.alerts == ["", ""]
    query.edit_message_text = original_edit
    query.id = "pass1"
    await handler._handle_word_chain_callback(update, query, None, f"chain:{state['round_id']}:0:pass")
    query.id = "pass2"
    await handler._handle_word_chain_callback(update, query, None, f"chain:{state['round_id']}:0:pass")
    assert (await service.current(-100, None))["turn_count"] == 1
    assert any("eski" in alert.lower() for alert in query.alerts)


@pytest.mark.asyncio
async def test_delayed_lobby_command_cannot_mutate_a_new_round():
    service = WordChainService(Repository())
    _, state = await service.open(chat_id=-100, thread_id=None, user_id=1, user_name="A")
    with pytest.raises(ValueError, match="stale_round"):
        await service.act(chat_id=-100, thread_id=None, user_id=2, action_id="cmd:2",
                          action="join", submitted_at=state["created_at"] - 2)


@pytest.mark.asyncio
async def test_panel_delivery_is_leased_across_runtime_instances():
    repository = Repository()
    one, two = WordChainService(repository), WordChainService(repository)
    _, state = await one.open(chat_id=-100, thread_id=None, user_id=1, user_name="A")
    state.update(panel_dirty=True, version=state["version"] + 1)
    await repository.compare_and_save_game_state(-100, one._key(None), state, expected_version=state["version"] - 1)
    claimed = await one.claim_panel(chat_id=-100, thread_id=None, now=100)
    assert claimed is not None
    assert await two.claim_panel(chat_id=-100, thread_id=None, now=101) is None
    assert await two.claim_panel(chat_id=-100, thread_id=None, now=131) is not None


@pytest.mark.asyncio
async def test_dispatcher_does_not_acknowledge_before_game_error_alert():
    from types import SimpleNamespace
    from src.handlers.callback_handler import CallbackHandler
    service = WordChainService(Repository())
    _, state = await service.open(chat_id=-100, thread_id=None, user_id=1, user_name="A")
    class Query:
        id = "one-player-start"
        data = f"chain:{state['round_id']}:0:start"
        message = SimpleNamespace(message_thread_id=None)
        def __init__(self):
            self.answers = []
        async def answer(self, text="", **kwargs):
            self.answers.append((text, kwargs))
    query = Query()
    handler = CallbackHandler.__new__(CallbackHandler)
    handler.word_chain_service = service
    event = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1, first_name="A"),
                            effective_chat=SimpleNamespace(id=-100, type="supergroup"))
    await handler.handle_callback_query(event, None)
    assert len(query.answers) == 1
    assert query.answers[0][1]["show_alert"] is True
    assert "iki" in query.answers[0][0]


@pytest.mark.asyncio
async def test_normal_group_reply_thread_does_not_hide_lobby_from_join_button():
    from types import SimpleNamespace
    from src.handlers.callback_handler import CallbackHandler
    service = WordChainService(Repository())
    _, state = await service.open(chat_id=-100, thread_id=None, user_id=1, user_name="A")
    answers, edits = [], []
    async def answer(text="", **kwargs): answers.append(text)
    async def edit(text, **kwargs): edits.append(text)
    query = SimpleNamespace(id="real-reply-join", data=f"chain:{state['round_id']}:0:join",
        message=SimpleNamespace(message_thread_id=83, is_topic_message=False),
        answer=answer, edit_message_text=edit)
    handler = CallbackHandler.__new__(CallbackHandler)
    handler.word_chain_service = service
    event = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=2, first_name="B"),
                            effective_chat=SimpleNamespace(id=-100, type="supergroup", is_forum=False))
    await handler.handle_callback_query(event, None)
    assert len((await service.current(-100, None))["players"]) == 2
    assert edits and answers == [""]


@pytest.mark.parametrize("topic,flag,expected", [(83,False,None),(7,True,7),(None,False,None)])
def test_forum_scope_requires_actual_topic_flag(topic, flag, expected):
    from types import SimpleNamespace
    from src.storage.game_scope import forum_thread_id
    assert forum_thread_id(SimpleNamespace(message_thread_id=topic,is_topic_message=flag)) == expected


@pytest.mark.asyncio
async def test_new_match_button_reply_opens_normal_group_lobby():
    from types import SimpleNamespace
    from src.handlers.command_handler import CommandHandler
    service = WordChainService(Repository())
    sent=[]
    async def reply(text, **kwargs): sent.append(text)
    message=SimpleNamespace(message_thread_id=95,is_topic_message=False,reply_text=reply)
    handler=CommandHandler.__new__(CommandHandler)
    handler.word_chain_service=service
    event=SimpleNamespace(effective_message=message,effective_chat=SimpleNamespace(id=-100,type="supergroup"),
                          effective_user=SimpleNamespace(id=1,first_name="A"))
    await handler.handle_word_chain_command(event,SimpleNamespace(args=[]))
    assert (await service.current(-100,None))["phase"] == "lobby"
    assert await service.current(-100,95) is None
    assert sent
