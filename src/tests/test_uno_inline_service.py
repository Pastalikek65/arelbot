"""Scoped UNO inline queries and choices never use process-local game state."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
import sys

import pytest

from src.services.uno_inline_service import UnoInlineService


SIGNING_FIXTURE = "fixture-only-uno-inline-secret-32-characters"


class UnoFixture:
    def __init__(self):
        self.state = {
            "game_key": "uno", "status": "playing", "chat_id": -10012345,
            "thread_id": 77, "round_id": "1234567890abcdef", "revision": 4,
            "players": [
                {"user_id": 10, "name": "Ali", "hand": [{"id": "r5-0", "color": "red", "kind": "5"}]},
                {"user_id": 20, "name": "Ayşe", "hand": [{"id": "b3-0", "color": "blue", "kind": "3"}]},
            ],
            "current_player_id": 10, "prompt_message_id": 200,
            "discard": [{"id": "r2-0", "color": "red", "kind": "2"}],
            "current_color": "red", "round_number": 1, "match_target": 500,
            "direction": 1, "turn_seconds": 60,
        }
        self.actions = []
        self.edits = []
        self.deferred = []
        self.lock = asyncio.Lock()

    async def current(self, chat_id):
        assert chat_id == self.state["chat_id"]
        return deepcopy(self.state)

    def presentation_lock(self, chat_id):
        assert chat_id == self.state["chat_id"]
        return self.lock

    async def action(self, **kwargs):
        assert kwargs["expected_revision"] == self.state["revision"]
        self.actions.append(kwargs)
        self.state["revision"] += 1
        return deepcopy(self.state)

    async def mark_panel_delivered(self, chat_id, revision, message_id):
        self.edits.append((chat_id, revision, message_id))

    async def defer_delivery(self, chat_id):
        self.deferred.append(chat_id)


class InlineQuery:
    def __init__(self, text, actor=10, offset=""):
        self.query = text
        self.from_user = SimpleNamespace(id=actor)
        self.offset = offset
        self.answers = []

    async def answer(self, **kwargs):
        self.answers.append(kwargs)


class Bot:
    def __init__(self):
        self.edits = []

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)


def fixture_ui(monkeypatch):
    calls = []

    def build_inline_results(state, *, actor_id, file_ids, encode_action, offset=0):
        calls.append((actor_id, offset))
        assert state["chat_id"] == -10012345
        assert file_ids == {"r_5": "fixture-file-id"}
        return [SimpleNamespace(id=encode_action("play", "r5-0")),
                SimpleNamespace(id=encode_action("noop", ""))], "2" if offset == 0 else ""

    monkeypatch.setitem(sys.modules, "src.services.uno_inline_ui",
                        SimpleNamespace(build_inline_results=build_inline_results))
    return calls


def controller():
    uno = UnoFixture()
    return UnoInlineService(uno, secret=SIGNING_FIXTURE, file_ids={"r_5": "fixture-file-id"}), uno


def test_real_noop_encoder_is_unique_for_each_card_and_info():
    service, uno = controller()
    uno.state["players"][0]["hand"] = [{"id": f"card-{i}"} for i in range(108)]
    ids = [service._encode_choice(uno.state, 10, "noop", f"card-{i}") for i in range(108)]
    ids += [service._encode_choice(uno.state, 10, "noop", value) for value in ("info", "notice")]
    assert len(set(ids)) == 110
    assert all(len(value.encode()) <= 64 and service._parse_choice(value) for value in ids)


@pytest.mark.asyncio
async def test_query_is_personal_scoped_and_pagination_is_actor_bound(monkeypatch):
    calls = fixture_ui(monkeypatch)
    service, uno = controller()
    query = InlineQuery(service.create_query(uno.state))
    assert await service.answer_query(SimpleNamespace(inline_query=query), SimpleNamespace())
    first = query.answers[0]
    assert first["cache_time"] == 0 and first["is_personal"] is True
    assert len(first["results"]) == 2
    assert all(len(result.id.encode()) <= 64 for result in first["results"])
    assert first["next_offset"] and calls == [(10, 0)]
    second = InlineQuery(query.query, offset=first["next_offset"])
    await service.answer_query(SimpleNamespace(inline_query=second), SimpleNamespace())
    assert calls == [(10, 0), (10, 2)]
    other_actor = InlineQuery(query.query, actor=20, offset=first["next_offset"])
    await service.answer_query(SimpleNamespace(inline_query=other_actor), SimpleNamespace())
    assert other_actor.answers[0]["results"] == []
    assert other_actor.answers[0]["next_offset"] == ""


@pytest.mark.asyncio
async def test_forged_scope_and_nonmember_never_reveal_hand(monkeypatch):
    calls = fixture_ui(monkeypatch)
    service, uno = controller()
    text = service.create_query(uno.state)
    for invalid in (text[:-1] + ("A" if text[-1] != "A" else "B"), text, "uno invalid"):
        query = InlineQuery(invalid, actor=99 if invalid == text else 10)
        await service.answer_query(SimpleNamespace(inline_query=query), SimpleNamespace())
        assert query.answers[0]["results"] == []
    assert calls == []


@pytest.mark.asyncio
async def test_signed_choice_mutates_once_and_edits_only_canonical_panel(monkeypatch):
    fixture_ui(monkeypatch)
    service, uno = controller()
    query_text = service.create_query(uno.state)
    query = InlineQuery(query_text)
    await service.answer_query(SimpleNamespace(inline_query=query), SimpleNamespace())
    chosen = SimpleNamespace(result_id=query.answers[0]["results"][0].id,
                             from_user=SimpleNamespace(id=10), query=query_text)
    bot = Bot()
    context = SimpleNamespace(bot=bot)
    assert await service.chosen_result(SimpleNamespace(chosen_inline_result=chosen), context)
    assert uno.actions[0]["action"] == "playpos"
    assert uno.actions[0]["card_position"] == 1
    assert uno.actions[0]["action_id"] == f"inline:{chosen.result_id}"
    assert bot.edits[0]["chat_id"] == -10012345 and bot.edits[0]["message_id"] == 200
    assert uno.edits == [(-10012345, 5, 200)]
    await service.chosen_result(SimpleNamespace(chosen_inline_result=chosen), context)
    assert len(uno.actions) == len(bot.edits) == 1


@pytest.mark.asyncio
async def test_noop_wrong_actor_and_stale_revision_do_not_mutate(monkeypatch):
    fixture_ui(monkeypatch)
    service, uno = controller()
    text = service.create_query(uno.state)
    query = InlineQuery(text)
    await service.answer_query(SimpleNamespace(inline_query=query), SimpleNamespace())
    playable, noop = [item.id for item in query.answers[0]["results"]]
    for result_id, actor in ((noop, 10), (playable, 20)):
        chosen = SimpleNamespace(result_id=result_id, from_user=SimpleNamespace(id=actor), query=text)
        await service.chosen_result(SimpleNamespace(chosen_inline_result=chosen), SimpleNamespace(bot=Bot()))
    uno.state["revision"] += 1
    stale = SimpleNamespace(result_id=playable, from_user=SimpleNamespace(id=10), query=text)
    await service.chosen_result(SimpleNamespace(chosen_inline_result=stale), SimpleNamespace(bot=Bot()))
    assert uno.actions == []


@pytest.mark.asyncio
async def test_real_gallery_contains_only_actor_hand_and_safe_bad_page():
    uno = UnoFixture()
    service = UnoInlineService(uno, secret=SIGNING_FIXTURE, file_ids={
        "option_draw": "fixture-draw", "option_info": "fixture-info",
        "colored:r_5": "fixture-red-five", "grey:r_5": "fixture-grey-red-five",
    })
    text = service.create_query(uno.state)
    query = InlineQuery(text)
    await service.answer_query(SimpleNamespace(inline_query=query), SimpleNamespace())
    results = query.answers[0]["results"]
    assert len(results) == 3
    assert [result.sticker_file_id for result in results if hasattr(result, "sticker_file_id")] == [
        "fixture-draw", "fixture-red-five", "fixture-info",
    ]
    assert all("b3-0" not in str(result) for result in results)

    bad_page = service._encode_offset(uno.state["chat_id"], uno.state["round_id"],
                                      uno.state["revision"], 10, 500)
    query = InlineQuery(text, offset=bad_page)
    await service.answer_query(SimpleNamespace(inline_query=query), SimpleNamespace())
    assert query.answers[0]["results"] == []
    assert query.answers[0]["next_offset"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(("source_action", "value", "service_action", "extra"), [
    ("draw", None, "draw", {}),
    ("pass", None, "skip", {}),
    ("bluff", None, "call_bluff", {}),
    ("color", "blue", "color", {"color": "blue"}),
])
async def test_signed_noncard_actions_map_to_durable_service(source_action, value, service_action, extra):
    service, uno = controller()
    result_id = service._encode_choice(uno.state, 10, source_action, value)
    choice = SimpleNamespace(result_id=result_id, from_user=SimpleNamespace(id=10),
                             query=service.create_query(uno.state))
    assert await service.chosen_result(SimpleNamespace(chosen_inline_result=choice),
                                       SimpleNamespace(bot=Bot()))
    assert uno.actions[0]["action"] == service_action
    for key, expected in extra.items():
        assert uno.actions[0][key] == expected
