"""Signed, private inline choices for Pişti and Papaz Kimde."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.services.card_table_inline_service import CardTableInlineService


SECRET = "s" * 32


def state(game="pisti", *, thread=8):
    return {
        "game_key": game, "chat_id": -1001234567890, "thread_id": thread,
        "round_id": "012345abcdef", "phase": "playing", "phase_seq": 3,
        "version": 14, "phase_ready": True, "turn_index": 0,
        "players": [{"id": 11, "active": True}, {"id": 22, "active": True},
                    {"id": 33, "active": True}],
        "hands": {"11": ["AS", "10H"], "22": ["KS", "QD"], "33": ["JC"]},
        "private_recipients": [11, 22, 33],
        "private_panels": {"11": {"phase_seq": 3, "message_id": 911}},
    }


class Service:
    def __init__(self, row):
        self.row = deepcopy(row)
        self.calls = []

    async def current(self, **scope):
        if (scope["game_key"], scope["chat_id"], scope["thread_id"]) != (
                self.row["game_key"], self.row["chat_id"], self.row["thread_id"]):
            return None
        return deepcopy(self.row)

    async def act(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["phase_seq"] != self.row["phase_seq"] or kwargs["expected_version"] != self.row["version"]:
            return "ignored", deepcopy(self.row)
        if kwargs["private_message_id"] != self.row["private_panels"]["11"]["message_id"]:
            raise ValueError("ct_stale_hand")
        self.row["phase_seq"] += 1
        self.row["version"] += 1
        return "applied", deepcopy(self.row)


class Query:
    def __init__(self, query, actor=11, offset=""):
        self.query, self.offset = query, offset
        self.from_user = SimpleNamespace(id=actor)
        self.answers = []

    async def answer(self, **kwargs):
        self.answers.append(kwargs)


async def ask(controller, text, *, actor=11, offset=""):
    query = Query(text, actor, offset)
    handled = await controller.answer_query(SimpleNamespace(inline_query=query), SimpleNamespace())
    assert handled
    return query.answers[0]


def chosen(result_id, query, actor=11):
    return SimpleNamespace(chosen_inline_result=SimpleNamespace(
        result_id=result_id, query=query,
        from_user=SimpleNamespace(id=actor, first_name="Player")))


@pytest.mark.asyncio
async def test_pisti_only_current_actor_own_cards_and_receipt_cas():
    service = Service(state())
    delivered = []

    async def on_applied(context, row):
        delivered.append(row["phase_seq"])

    controller = CardTableInlineService(service, SECRET,
        {"card:spades_A": "sticker-as", "card:hearts_10": "sticker-10h",
         "card:spades_K": "sticker-ks"},
        on_applied=on_applied)
    text = controller.create_query(service.row)
    answer = await ask(controller, text)
    assert answer["cache_time"] == 0 and answer["is_personal"] is True
    assert [result.sticker_file_id for result in answer["results"]] == ["sticker-as", "sticker-10h"]
    assert all(len(result.id.encode()) <= 64 for result in answer["results"])
    assert (await ask(controller, text, actor=22))["results"] == []
    result_id = answer["results"][1].id
    assert await controller.chosen_result(chosen(result_id, text), SimpleNamespace())
    assert len(service.calls) == 1
    assert service.calls[0]["action"] == "play" and service.calls[0]["value"] == 1
    assert service.calls[0]["private_message_id"] == 911
    assert service.calls[0]["expected_version"] == 14
    assert delivered == [4]
    assert await controller.chosen_result(chosen(result_id, text), SimpleNamespace())
    assert len(service.calls) == 1


@pytest.mark.asyncio
async def test_signed_scope_actor_receipt_and_stale_version_fail_closed():
    service = Service(state())
    controller = CardTableInlineService(service, SECRET,
        {"card:spades_A": "face", "card:hearts_10": "face2"})
    text = controller.create_query(service.row)
    answer = await ask(controller, text)
    result_id = answer["results"][0].id
    assert (await ask(controller, text[:-1] + "x"))["results"] == []
    assert (await ask(controller, text.replace("012345abcdef", "112345abcdef")))["results"] == []
    assert await controller.chosen_result(chosen(result_id, text, actor=22), SimpleNamespace())
    assert await controller.chosen_result(chosen(result_id[:-1] + "x", text), SimpleNamespace())
    assert service.calls == []
    service.row["private_panels"]["11"]["message_id"] = 912
    assert await controller.chosen_result(chosen(result_id, text), SimpleNamespace())
    assert service.calls == []
    service.row["private_panels"]["11"]["message_id"] = 911
    service.row["version"] += 1
    assert await controller.chosen_result(chosen(result_id, text), SimpleNamespace())
    assert service.calls == []


@pytest.mark.asyncio
async def test_papaz_uses_only_back_faces_for_opponent_and_indexed_draw():
    row = state("papaz_kimde")
    service = Service(row)
    controller = CardTableInlineService(service, SECRET,
        {"card:spades_A": "own-as", "card:hearts_10": "own-10h",
         "back": "back"})
    text = controller.create_query(row)
    answer = await ask(controller, text)
    ids = [result.sticker_file_id for result in answer["results"]]
    assert ids == ["back", "back"]
    draw_id = answer["results"][-1].id
    assert await controller.chosen_result(chosen(draw_id, text), SimpleNamespace())
    assert len(service.calls) == 1
    assert service.calls[0]["action"] == "draw" and service.calls[0]["value"] == 1


@pytest.mark.asyncio
async def test_offset_is_bound_to_actor_scope_phase_and_version():
    row = state()
    row["hands"]["11"] = ["AS"] * 55
    service = Service(row)
    controller = CardTableInlineService(service, SECRET, {"card:spades_A": "face"})
    text = controller.create_query(row)
    first = await ask(controller, text)
    assert len(first["results"]) == 50 and first["next_offset"]
    second = await ask(controller, text, offset=first["next_offset"])
    assert len(second["results"]) == 5 and second["next_offset"] == ""
    assert (await ask(controller, text, offset=first["next_offset"][:-1] + "x"))["results"] == []
    service.row["version"] += 1
    assert (await ask(controller, text, offset=first["next_offset"]))["results"] == []


def test_secret_and_round_validation():
    with pytest.raises(ValueError):
        CardTableInlineService(Service(state()), "short", {})
    controller = CardTableInlineService(Service(state()), SECRET, {})
    bad = state()
    bad["round_id"] = "oops"
    with pytest.raises(ValueError):
        controller.create_query(bad)
