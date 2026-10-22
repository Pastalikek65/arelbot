"""Kenney card gallery and private hand boundaries."""

from hashlib import sha256
import json
from pathlib import Path

import pytest
from PIL import Image
from telegram import InlineQueryResultCachedSticker

from src.services.playing_card_ui import card_key, load_sticker_ids
from src.services.playing_card_ui import render_card_backs_inline, render_cards_inline


ASSETS = Path(__file__).resolve().parents[2] / "assets" / "playing_cards"


def _encode(action, value):
    return f"{action}:{value or '-'}"


def _files():
    return {**{f"card:{p.stem}": f"card-{p.stem}"
               for p in (ASSETS / "stickers").glob("*.png") if p.stem != "back"},
            **{f"grey:{p.stem}": f"grey-{p.stem}"
               for p in (ASSETS / "grey").glob("*.png")},
            "back": "card-back"}


def _state():
    return {"phase": "playing", "current_turn": 1,
            "players": [{"id": 1}, {"id": 2}],
            "hands": {"1": [["A", "♠"], ["10", "♥"], ["2", "♣"]],
                      "2": [["K", "♦"]]}}


@pytest.mark.parametrize(("card", "expected"), [
    (("A", "♠"), "spades_A"), (["10", "♥"], "hearts_10"),
    ({"rank": "Q", "suit": "♦"}, "diamonds_Q"), ("2C", "clubs_2"),
    ("AS", "spades_A"), (["A", "♠️"], "spades_A"),
])
def test_card_key_supports_batak_pisti_and_explicit_card_shapes(card, expected):
    assert card_key(card) == expected


@pytest.mark.parametrize("card", ["joker", "1S", ["A", "?"], {"rank": "A"}, ()])
def test_card_key_rejects_nonstandard_cards(card):
    with pytest.raises(ValueError, match="playing_card_invalid"):
        card_key(card)


def test_only_actors_hand_is_rendered_and_illegal_cards_are_noop():
    state = _state()
    page, next_offset = render_cards_inline(state, actor_id=1, file_ids=_files(),
                                            encode_action=_encode, playable_indices={0, 2})
    assert next_offset == ""
    assert [item.id for item in page] == ["play:0", "noop:1", "play:2"]
    assert page[0].sticker_file_id == "card-spades_A"
    assert page[1].sticker_file_id == "grey-hearts_10"
    assert page[1].input_message_content.message_text.startswith("Bu kart")
    assert "diamonds_K" not in str(page)

    page, _ = render_cards_inline(state, actor_id=2, file_ids=_files(),
                                  encode_action=_encode, playable_indices={0})
    assert [item.id for item in page] == ["noop:0"]
    assert page[0].sticker_file_id == "grey-diamonds_K"
    assert "spades_A" not in str(page)


def test_card_table_turn_index_is_respected():
    state = _state()
    state.pop("current_turn")
    state["turn_index"] = 1
    page, _ = render_cards_inline(state, actor_id=1, file_ids=_files(),
                                  encode_action=_encode, playable_indices={0})
    assert page[0].id == "noop:0"
    page, _ = render_cards_inline(state, actor_id=2, file_ids=_files(),
                                  encode_action=_encode, playable_indices={0})
    assert page[0].id == "play:0"


def test_nonparticipant_and_finished_game_never_show_hand():
    state = _state()
    page, _ = render_cards_inline(state, actor_id=3, file_ids=_files(),
                                  encode_action=_encode, playable_indices={0})
    assert len(page) == 1 and page[0].id == "noop:notice"
    assert "spades_A" not in str(page)
    state["phase"] = "finished"
    page, _ = render_cards_inline(state, actor_id=1, file_ids=_files(),
                                  encode_action=_encode, playable_indices={0})
    assert len(page) == 1 and page[0].id == "noop:notice"
    assert "spades_A" not in str(page)


def test_105_cards_paginate_without_duplicates_or_overflow():
    state = _state()
    state["hands"]["1"] = ["AS"] * 105
    sizes, seen, offset = [], set(), 0
    while True:
        page, following = render_cards_inline(state, actor_id=1,
            file_ids=_files(), encode_action=_encode,
            playable_indices=range(105), offset=offset)
        sizes.append(len(page))
        assert len(page) <= 50
        ids = {item.id for item in page}
        assert seen.isdisjoint(ids)
        seen.update(ids)
        if not following:
            break
        offset = int(following)
    assert sizes == [50, 50, 5]
    assert "play:104" in seen
    with pytest.raises(ValueError, match="playing_card_invalid_offset"):
        render_cards_inline(state, actor_id=1, file_ids=_files(),
                            encode_action=_encode, offset=106)


def test_card_back_only_carries_hidden_count_and_never_a_face():
    page, following = render_card_backs_inline(52, file_ids=_files(), encode_action=_encode)
    assert len(page) == 50 and following == "50"
    assert all(isinstance(item, InlineQueryResultCachedSticker) for item in page)
    assert all(item.sticker_file_id == "card-back" for item in page)
    assert all(item.id.startswith("noop:back:") for item in page)
    second, following = render_card_backs_inline(52, file_ids=_files(),
                                                   encode_action=_encode, offset=50)
    assert len(second) == 2 and following == ""
    chosen, _ = render_card_backs_inline(2, file_ids=_files(),
                                         encode_action=_encode, action="draw")
    assert [item.id for item in chosen] == ["draw:back:0", "draw:back:1"]
    assert all(item.sticker_file_id == "card-back" for item in chosen)


def test_registry_returns_empty_until_provisioned_and_requires_all_105_keys(tmp_path):
    registry = tmp_path / "telegram_stickers.json"
    assert load_sticker_ids(registry) == {}
    registry.write_text(json.dumps({"card:spades_A": "one"}), encoding="utf-8")
    with pytest.raises(ValueError, match="playing_card_incomplete_registry"):
        load_sticker_ids(registry)
    registry.write_text(json.dumps({"file_ids": _files()}), encoding="utf-8")
    assert load_sticker_ids(registry) == _files()


def test_originals_and_512px_derivatives_match_manifest():
    source = json.loads((ASSETS / "SOURCE.json").read_text(encoding="utf-8"))
    assert source["license"] == "CC0-1.0"
    assert len(source["sha256"]) == 160
    assert sha256((ASSETS / "LICENSE.txt").read_bytes()).hexdigest() == source["license_sha256"]
    for relative, expected in source["sha256"].items():
        path = ASSETS / relative
        assert sha256(path.read_bytes()).hexdigest() == expected
        if relative.endswith(".png"):
            with Image.open(path) as image:
                assert image.size == ((140, 190) if relative.startswith("original/") else (512, 512))
        if relative.startswith(("stickers/", "grey/")):
            assert path.stat().st_size <= 512 * 1024


def test_locked_faces_differ_even_for_black_suits_without_covering_face():
    for face in (ASSETS / "stickers").glob("*.png"):
        if face.stem == "back":
            continue
        grey = ASSETS / "grey" / face.name
        assert face.read_bytes() != grey.read_bytes()
        with Image.open(face) as a, Image.open(grey) as b:
            a, b = a.convert("RGBA"), b.convert("RGBA")
            mask = a.getchannel("A").point(lambda alpha: 255 if alpha else 0)
            assert Image.composite(b, a, mask).tobytes() == a.tobytes()
