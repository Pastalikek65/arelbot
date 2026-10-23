"""maildian sticker gallery preserves hand secrecy and actionable paging."""

from hashlib import sha256
import json
from pathlib import Path
import random

import pytest
from telegram import InlineQueryResultArticle, InlineQueryResultCachedSticker

from src.services.uno_inline_ui import MAX_RESULTS, build_inline_results, card_asset_key
from src.services.uno_ui import render_uno_hand_alert, render_uno_text, uno_keyboard


ASSETS = Path(__file__).resolve().parents[2] / "assets" / "uno_maildian"


def _file_ids():
    ids = {}
    for folder, prefix in (("png", "colored"), ("png_lowsat", "grey")):
        for path in (ASSETS / folder).glob("*.png"):
            ids[f"{prefix}:{path.stem}"] = f"file-{prefix}-{path.stem}"
    for option in ("draw", "pass", "bluff", "info"):
        ids[f"option_{option}"] = f"file-option-{option}"
    return ids


def _state():
    return {
        "status": "playing", "current_player_id": 1, "current_color": "red",
        "discard": [{"id": "top", "color": "red", "kind": "5"}],
        "players": [
            {"user_id": 1, "name": "Bir", "drawn_card_id": None, "hand": [
                {"id": "mine-red", "color": "red", "kind": "7"},
                {"id": "mine-blue", "color": "blue", "kind": "1"},
                {"id": "mine-wild", "color": "black", "kind": "wild"},
            ]},
            {"user_id": 2, "name": "İki", "hand": [
                {"id": "secret-other", "color": "green", "kind": "9"},
            ]},
        ],
    }


def _encode(action, value):
    return f"{action}:{value or '-'}"


def test_card_keys_cover_upstream_number_action_and_wild_art():
    assert card_asset_key({"color": "blue", "kind": "0"}) == "b_0"
    assert card_asset_key({"color": "yellow", "kind": "draw2"}) == "y_draw"
    assert card_asset_key({"color": "green", "kind": "skip"}) == "g_skip"
    assert card_asset_key({"color": "red", "kind": "reverse"}) == "r_reverse"
    assert card_asset_key({"color": "black", "kind": "wild"}) == "colorchooser"
    assert card_asset_key({"color": "black", "kind": "draw4"}) == "draw_four"
    with pytest.raises(ValueError, match="uno_inline_invalid_card"):
        card_asset_key({"color": "black", "kind": "7"})
    with pytest.raises(ValueError, match="uno_inline_invalid_card"):
        card_asset_key({"color": "red", "kind": ""})


def test_playable_cards_colored_illegal_grey_and_other_hand_never_listed():
    results, next_offset = build_inline_results(_state(), actor_id=1,
                                                 file_ids=_file_ids(), encode_action=_encode)
    by_id = {item.id: item for item in results}
    assert next_offset == ""
    assert isinstance(by_id["draw:-"], InlineQueryResultCachedSticker)
    assert by_id["play:mine-red"].sticker_file_id == "file-colored-r_7"
    assert by_id["play:mine-wild"].sticker_file_id == "file-colored-colorchooser"
    assert by_id["noop:mine-blue"].sticker_file_id == "file-grey-b_1"
    assert by_id["noop:mine-blue"].input_message_content.message_text.startswith("Sıra:")
    assert "secret-other" not in str(results)
    assert "Yeşil 9" not in str(results)


def test_non_actor_sees_only_own_grey_hand_without_actions():
    results, _ = build_inline_results(_state(), actor_id=2, file_ids=_file_ids(),
                                      encode_action=_encode)
    assert [item.id for item in results] == ["noop:secret-other"]
    assert results[0].sticker_file_id == "file-grey-g_9"
    assert "mine-red" not in str(results)


def test_drawn_card_color_choice_and_bluff_options_follow_real_state():
    state = _state()
    state["players"][0]["drawn_card_id"] = "mine-red"
    results, _ = build_inline_results(state, actor_id=1, file_ids=_file_ids(),
                                      encode_action=_encode)
    ids = [item.id for item in results]
    assert "pass:-" in ids and "draw:-" not in ids
    assert "play:mine-red" in ids
    assert "noop:mine-wild" in ids

    state["awaiting_color"] = True
    results, _ = build_inline_results(state, actor_id=1, file_ids=_file_ids(),
                                      encode_action=_encode)
    assert {item.id for item in results if isinstance(item, InlineQueryResultArticle)} == {
        "color:red", "color:blue", "color:green", "color:yellow"}
    assert not any(item.id.startswith("play:") for item in results)

    state["awaiting_color"] = False
    state["players"][0]["drawn_card_id"] = None
    state["discard"] = [{"id": "wild", "color": "black", "kind": "draw4"}]
    state["pending_draw"] = 4
    ids = [item.id for item in build_inline_results(state, actor_id=1,
            file_ids=_file_ids(), encode_action=_encode)[0]]
    assert "bluff:-" in ids and "draw:-" in ids


def test_blue_cards_after_draw_explain_why_only_new_card_is_unlocked():
    state = _state()
    state["discard"] = [{"id": "top-blue", "color": "blue", "kind": "8"}]
    state["current_color"] = "blue"
    state["players"][0]["hand"].append(
        {"id": "new-blue", "color": "blue", "kind": "5"})
    before = {item.id for item in build_inline_results(state, actor_id=1,
              file_ids=_file_ids(), encode_action=_encode)[0]}
    assert {"play:mine-blue", "play:new-blue"} <= before

    state["players"][0]["drawn_card_id"] = "new-blue"
    after = {item.id for item in build_inline_results(state, actor_id=1,
             file_ids=_file_ids(), encode_action=_encode)[0]}
    assert "noop:mine-blue" in after and "play:new-blue" in after
    assert "Yalnız yeni çekilen kart" in render_uno_text(state)


def test_game_info_shows_chosen_color_after_wild():
    state = _state()
    state["discard"] = [{"id": "wild", "color": "black", "kind": "wild"}]
    state["current_color"] = "green"
    results, _ = build_inline_results(state, actor_id=1, file_ids=_file_ids(),
                                      encode_action=_encode)
    info = next(item for item in results if item.id == "noop:info")
    assert "Üst kart: Yeşil Joker" in info.input_message_content.message_text


def test_105_card_hand_pages_have_at_most_fifty_unique_results():
    state = _state()
    state["players"][0]["hand"] = [
        {"id": f"card-{index}", "color": "red", "kind": "7"} for index in range(105)]
    offset, seen, sizes = 0, set(), []
    while True:
        page, next_offset = build_inline_results(state, actor_id=1,
            file_ids=_file_ids(), encode_action=_encode, offset=offset)
        sizes.append(len(page))
        assert 1 <= len(page) <= MAX_RESULTS
        ids = {item.id for item in page}
        assert seen.isdisjoint(ids)
        seen.update(ids)
        if not next_offset:
            break
        offset = int(next_offset)
    assert sizes == [50, 50, 7]
    assert "play:card-104" in seen
    with pytest.raises(ValueError, match="uno_inline_invalid_offset"):
        build_inline_results(state, actor_id=1, file_ids=_file_ids(),
                             encode_action=_encode, offset=999)


def test_maildian_engine_gallery_uses_adapter_legality():
    from src.games import uno_upstream

    state = uno_upstream.new_uno_state(chat_id=9, owner_id=1, owner_name="Bir")
    state = uno_upstream.join_player(state, user_id=2, name="İki")
    state = uno_upstream.start_round(state, rng=random.Random(7))
    actor_id = state["current_player_id"]
    results, _ = build_inline_results(state, actor_id=actor_id,
                                      file_ids=_file_ids(), encode_action=_encode)
    hand = next(p["hand"] for p in state["players"] if p["user_id"] == actor_id)
    playable = {card["id"] for card in hand
                if uno_upstream.can_play(state, card, player_id=actor_id)}
    shown = {item.id.removeprefix("play:") for item in results
             if item.id.startswith("play:")}
    assert shown == playable


def test_real_adapter_exhausted_draw_shows_pass_for_actor_only():
    from src.games import uno_upstream

    state = uno_upstream.new_uno_state(chat_id=9, owner_id=1, owner_name="Bir")
    state = uno_upstream.join_player(state, user_id=2, name="İki")
    state = uno_upstream.start_round(state, rng=random.Random(7))
    actor = state["current_player_id"]
    other = next(p for p in state["players"] if p["user_id"] != actor)
    other["hand"].extend(state["deck"])
    state["deck"] = []
    assert uno_upstream.validate_state(state)
    state = uno_upstream.draw_card(state, user_id=actor, action_id="exhausted-draw")
    me = next(p for p in state["players"] if p["user_id"] == actor)
    assert me["draw_attempted"] and me["drawn_card_id"] is None
    assert not state.get("draw_attempted")  # The durable field belongs to the player.
    results, _ = build_inline_results(state, actor_id=actor,
                                      file_ids=_file_ids(), encode_action=_encode)
    ids = [item.id for item in results]
    assert "pass:-" in ids and "draw:-" not in ids
    assert "Pas geç" in render_uno_text(state)
    assert "pas geç" in render_uno_hand_alert(state, user_id=actor)
    buttons = [button for row in uno_keyboard(state).inline_keyboard for button in row]
    assert any(button.text == "⏭️ Pas geç" for button in buttons)
    assert not any(button.text == "🃏 Kart çek" for button in buttons)
    assert uno_upstream.skip_after_draw(state, user_id=actor, action_id="pass")


def test_inline_group_panel_keeps_one_current_uno_challenge():
    from src.games import uno_upstream

    state = uno_upstream.new_uno_state(chat_id=9, owner_id=1, owner_name="Bir")
    state = uno_upstream.join_player(state, user_id=2, name="İki")
    state = uno_upstream.start_round(state, rng=random.Random(7))
    victim = next(p for p in state["players"] if p["user_id"] != state["current_player_id"])
    removed = victim["hand"][1:]
    victim["hand"] = victim["hand"][:1]
    state["deck"].extend(removed)
    victim["uno_called"] = False
    state["uno_vulnerable_id"] = victim["user_id"]
    state["inline_query"] = "uno signed-scope"
    buttons = [button for row in uno_keyboard(state).inline_keyboard for button in row]
    challenge = [button for button in buttons if ":challenge:" in str(button.callback_data)]
    assert len(challenge) == 1
    assert challenge[0].callback_data.endswith(f":challenge:{victim['user_id']}")
    assert any(button.text == "📣 UNO de" for button in buttons)
    assert any(button.text == "🃏 Kartları gör" for button in buttons)
    assert uno_upstream.challenge_uno(state, user_id=state["current_player_id"],
                                      target_id=victim["user_id"], action_id="challenge")
    victim["uno_called"] = True
    assert not any(":challenge:" in str(button.callback_data)
                   for row in uno_keyboard(state).inline_keyboard for button in row)


def test_upstream_pngs_match_provenance_hashes():
    source = json.loads((ASSETS / "SOURCE.json").read_text(encoding="utf-8"))
    assert source["upstream_commit"] == "01640d1"
    assert source["png_count"] == 60 and source["png_lowsat_count"] == 54
    assert len(source["sha256"]) == 114
    for relative, expected in source["sha256"].items():
        assert sha256((ASSETS / relative).read_bytes()).hexdigest() == expected
