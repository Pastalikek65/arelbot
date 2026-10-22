"""Signed, actor-private Batak inline gallery and atomic turn tests."""

import asyncio
from copy import deepcopy
import random
from types import SimpleNamespace

import pytest

from src.games.batak_round import BatakRound
from src.services.batak_inline_service import BatakInlineService, card_key


SIGNING_FIXTURE = "batak-inline-test-key-longer-than-thirty-two-bytes"


class FakeBatak:
    def __init__(self):
        self.state = BatakRound.lobby(1, "Bir")
        BatakRound.join(self.state, 2, "İki")
        BatakRound.start(self.state, rng=random.Random(9))
        self.state["prompt_message_id"] = 100
        self.actions = []
        self.lock = asyncio.Lock()
        self.panel_lock = asyncio.Lock()

    def presentation_lock(self, chat_id):
        assert chat_id == -100
        return self.panel_lock

    async def snapshot(self, chat_id):
        assert chat_id == -100
        return deepcopy(self.state)

    async def play(self, *, chat_id, user_id, card_index, action_id, expected_turn_token,
                   expected_panel_message_id=None):
        async with self.lock:
            assert chat_id == -100
            if BatakRound.turn_token(self.state) != expected_turn_token:
                raise ValueError("stale_turn")
            if expected_panel_message_id != self.state["prompt_message_id"]:
                raise ValueError("stale_panel")
            if action_id in self.actions:
                raise ValueError("duplicate_action")
            self.actions.append(action_id)
            BatakRound.play(self.state, user_id, card_index)
            return {"state": deepcopy(self.state), "terminal": self.state["phase"] == "completed"}


class Query:
    def __init__(self, query, user_id):
        self.query = query
        self.from_user = SimpleNamespace(id=user_id)
        self.answered = []

    async def answer(self, **kwargs):
        self.answered.append(kwargs)


class Bot:
    def __init__(self, *, fail=False):
        self.edits = []
        self.fail = fail

    async def edit_message_text(self, **kwargs):
        if self.fail:
            raise RuntimeError("edit failed")
        self.edits.append(kwargs)


def fixture():
    batak = FakeBatak()
    media = {}
    for hand in batak.state["hands"].values():
        for card in hand:
            key = card_key(card)
            media[f"card:{key}"] = f"face-{key}"
            media[f"grey:{key}"] = f"grey-{key}"
    deferred = []

    async def defer(chat_id, state):
        deferred.append((chat_id, state["game_id"]))

    controller = BatakInlineService(
        batak, secret=SIGNING_FIXTURE, file_ids=media,
        render_public=lambda state: f"Batak {state['completed_tricks']}",
        keyboard=lambda state: None,
        defer_delivery=defer,
    )
    return controller, batak, deferred


@pytest.mark.asyncio
async def test_gallery_only_exposes_querying_players_hand_and_is_personal():
    controller, batak, _ = fixture()
    scope = controller.create_query(-100, batak.state)
    first = Query(scope, 1)
    second = Query(scope, 2)
    outsider = Query(scope, 3)
    for query in (first, second, outsider):
        assert await controller.answer_query(SimpleNamespace(inline_query=query), SimpleNamespace())
        assert query.answered[0]["is_personal"] and query.answered[0]["cache_time"] == 0
    assert len(first.answered[0]["results"]) == len(batak.state["hands"]["1"])
    assert len(second.answered[0]["results"]) == len(batak.state["hands"]["2"])
    assert outsider.answered[0]["results"] == []
    assert all(result.input_message_content is not None for result in second.answered[0]["results"])
    assert all(len(result.id.encode()) <= 64 for result in first.answered[0]["results"])


@pytest.mark.asyncio
async def test_player_with_no_cards_while_last_trick_finishes_sees_empty_notice():
    controller, batak, _ = fixture()
    batak.state["hands"]["1"] = []
    batak.state["current_turn"] = 2
    query = Query(controller.create_query(-100, batak.state), 1)
    await controller.answer_query(SimpleNamespace(inline_query=query), SimpleNamespace())
    results = query.answered[0]["results"]
    assert len(results) == 1 and results[0].title == "Elinde kart yok"


@pytest.mark.asyncio
async def test_signed_choice_plays_once_and_edits_canonical_panel():
    controller, batak, _ = fixture()
    scope = controller.create_query(-100, batak.state)
    query = Query(scope, 1)
    await controller.answer_query(SimpleNamespace(inline_query=query), SimpleNamespace())
    selected = query.answered[0]["results"][0]
    bot = Bot()
    choice = SimpleNamespace(result_id=selected.id, query=scope, from_user=SimpleNamespace(id=1))
    assert await controller.chosen_result(SimpleNamespace(chosen_inline_result=choice), SimpleNamespace(bot=bot))
    assert len(batak.actions) == 1
    assert bot.edits[0]["chat_id"] == -100 and bot.edits[0]["message_id"] == 100
    markup = bot.edits[0]["reply_markup"]
    assert any(button.switch_inline_query_current_chat == scope
               for row in markup.inline_keyboard for button in row)
    await controller.chosen_result(SimpleNamespace(chosen_inline_result=choice), SimpleNamespace(bot=bot))
    assert len(batak.actions) == len(bot.edits) == 1


@pytest.mark.asyncio
async def test_tampered_scope_wrong_actor_and_stale_turn_never_play():
    controller, batak, _ = fixture()
    scope = controller.create_query(-100, batak.state)
    query = Query(scope, 1)
    await controller.answer_query(SimpleNamespace(inline_query=query), SimpleNamespace())
    result_id = query.answered[0]["results"][0].id
    for signed_id, actor, signed_scope in (
        (result_id[:-1] + ("A" if result_id[-1] != "A" else "B"), 1, scope),
        (result_id, 2, scope),
        (result_id, 1, scope[:-1] + ("A" if scope[-1] != "A" else "B")),
    ):
        choice = SimpleNamespace(result_id=signed_id, query=signed_scope, from_user=SimpleNamespace(id=actor))
        await controller.chosen_result(SimpleNamespace(chosen_inline_result=choice), SimpleNamespace(bot=Bot()))
    assert batak.actions == []
    # A fresh turn invalidates every card in the old gallery even for its owner.
    BatakRound.play(batak.state, 1, 0)
    choice = SimpleNamespace(result_id=result_id, query=scope, from_user=SimpleNamespace(id=1))
    await controller.chosen_result(SimpleNamespace(chosen_inline_result=choice), SimpleNamespace(bot=Bot()))
    assert batak.actions == []


@pytest.mark.asyncio
async def test_failed_panel_edit_defers_recovery_after_persisted_move():
    controller, batak, deferred = fixture()
    scope = controller.create_query(-100, batak.state)
    query = Query(scope, 1)
    await controller.answer_query(SimpleNamespace(inline_query=query), SimpleNamespace())
    choice = SimpleNamespace(result_id=query.answered[0]["results"][0].id,
                             query=scope, from_user=SimpleNamespace(id=1))
    await controller.chosen_result(SimpleNamespace(chosen_inline_result=choice),
                                   SimpleNamespace(bot=Bot(fail=True)))
    assert len(batak.actions) == 1
    assert deferred == [(-100, batak.state["game_id"])]


def test_query_requires_current_canonical_panel_and_card_keys_are_stable():
    controller, batak, _ = fixture()
    batak.state.pop("prompt_message_id")
    with pytest.raises(ValueError, match="BATAK_INLINE_PANEL_NOT_READY"):
        controller.create_query(-100, batak.state)
    assert card_key(["10", "♠"]) == "spades_10"
    assert card_key(["A", "♥"]) == "hearts_A"


@pytest.mark.asyncio
async def test_concurrent_choices_cannot_overwrite_newer_panel_with_old_turn():
    controller, batak, _ = fixture()
    first_edit_started = asyncio.Event()
    release_first_edit = asyncio.Event()

    class SlowFirstBot(Bot):
        async def edit_message_text(self, **kwargs):
            if not self.edits:
                first_edit_started.set()
                await release_first_edit.wait()
            await super().edit_message_text(**kwargs)

    bot = SlowFirstBot()
    scope = controller.create_query(-100, batak.state)
    query = Query(scope, 1)
    await controller.answer_query(SimpleNamespace(inline_query=query), SimpleNamespace())
    first_id = next(result.id for result in query.answered[0]["results"]
                    if controller._parse_choice(result.id)[-1].startswith("p"))
    first = SimpleNamespace(result_id=first_id, query=scope, from_user=SimpleNamespace(id=1))
    first_task = asyncio.create_task(controller.chosen_result(
        SimpleNamespace(chosen_inline_result=first), SimpleNamespace(bot=bot)))
    await asyncio.wait_for(first_edit_started.wait(), timeout=1)
    assert len(batak.actions) == 1

    second_actor = int(batak.state["current_turn"])
    second_query = Query(controller.create_query(-100, batak.state), second_actor)
    await controller.answer_query(SimpleNamespace(inline_query=second_query), SimpleNamespace())
    second_id = next(result.id for result in second_query.answered[0]["results"]
                     if controller._parse_choice(result.id)[-1].startswith("p"))
    second = SimpleNamespace(result_id=second_id, query=second_query.query,
                             from_user=SimpleNamespace(id=second_actor))
    second_task = asyncio.create_task(controller.chosen_result(
        SimpleNamespace(chosen_inline_result=second), SimpleNamespace(bot=bot)))
    await asyncio.sleep(0)
    assert len(batak.actions) == 1 and bot.edits == []

    release_first_edit.set()
    assert await asyncio.wait_for(first_task, timeout=1)
    assert await asyncio.wait_for(second_task, timeout=1)
    assert len(batak.actions) == 2
    assert [edit["text"] for edit in bot.edits] == ["Batak 0", "Batak 1"]
