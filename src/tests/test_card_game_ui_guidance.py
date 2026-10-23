"""Gameplay guidance must agree with the domain state without exposing private cards."""

from src.games import card_table as table_rules
from src.games.uno import join_player, new_uno_state, start_round
from src.services.uno_ui import render_uno_hand_alert, render_uno_text, uno_keyboard
from src.ui import card_table_ui


def _uno():
    state = new_uno_state(chat_id=-100, owner_id=1, owner_name="Bir")
    state = join_player(state, user_id=2, name="İki")
    state = start_round(state)
    state["discard"] = [{"id": "top", "color": "red", "kind": "5"}]
    state["current_color"] = "red"
    state["players"][0]["hand"] = [
        {"id": "old", "color": "red", "kind": "7"},
        {"id": "new", "color": "red", "kind": "8"},
    ]
    return state


def _labels(state):
    return [button.text for row in uno_keyboard(state).inline_keyboard for button in row]


def test_uno_drawn_card_is_marked_only_in_private_alert():
    state = _uno()
    state["players"][0]["drawn_card_id"] = "new"
    labels = _labels(state)
    assert "🃏 #2" in labels
    assert "🃏 #1" in labels  # Public positions must not reveal playability.
    assert "🃏 Kart çek" not in labels
    assert "⏭️ Pas geç" in labels
    alert = render_uno_hand_alert(state, user_id=1)
    assert "1. Kırmızı 7 ✓" not in alert
    assert "2. Kırmızı 8 ✓ (yeni çekilen)" in alert
    assert "Çekilen kartı oyna veya pas geç" in render_uno_text(state)


def test_uno_public_positions_do_not_reveal_matching_color():
    state = _uno()
    state["players"][0]["hand"][1]["color"] = "blue"
    assert "🃏 #1" in _labels(state)
    assert "🃏 #2" in _labels(state)
    alert = render_uno_hand_alert(state, user_id=1)
    assert "1. Kırmızı 7 ✓" in alert
    assert "2. Mavi 8 ✓" not in alert


def test_uno_color_choice_blocks_play_and_explains_pending_draw():
    state = _uno()
    state.update(awaiting_color=True, pending_draw=4, current_color=None,
                 discard=[{"id": "wild", "color": "black", "kind": "draw4"}])
    text = render_uno_text(state)
    assert "Renk seç" in text
    assert "+4 ceza" in text
    assert "itiraz et" not in text
    assert "Renk: <b>—</b>" not in text
    assert "🃏 #1" not in _labels(state)
    assert "Kırmızı" in _labels(state)


def test_uno_eight_long_escaped_names_fit_telegram_panel_limit():
    long_name = "<&\"'" * 20
    state = new_uno_state(chat_id=-100, owner_id=1, owner_name=long_name)
    for user_id in range(2, 9):
        state = join_player(state, user_id=user_id, name=long_name)
    state = start_round(state)
    state["last_penalty"] = {"user_id": 2, "cards": 4}
    state["last_timeout_user_id"] = 3
    panel = render_uno_text(state)
    assert len(panel.encode("utf-16-le")) // 2 <= 4096
    assert state["players"][0]["name"] == long_name
    assert "&lt;" in panel and "&amp;" in panel


def test_large_uno_hand_pages_keep_buttons_bounded_and_all_positions_reachable():
    state = _uno()
    state["players"][0]["hand"] = [
        {"id": f"card-{i}", "color": "blue", "kind": "8"} for i in range(105)
    ]
    first = uno_keyboard(state)
    last = uno_keyboard(state, card_page=8)
    first_buttons = [button for row in first.inline_keyboard for button in row]
    last_buttons = [button for row in last.inline_keyboard for button in row]
    assert len(first_buttons) < 100 and len(last_buttons) < 100
    assert "🃏 #1" in [button.text for button in first_buttons]
    assert "🃏 #13" not in [button.text for button in first_buttons]
    assert "🃏 #105" in [button.text for button in last_buttons]
    assert "Sonraki kartlar ▶️" in [button.text for button in first_buttons]
    assert "◀️ Önceki kartlar" in [button.text for button in last_buttons]
    assert "🃏 #105" not in [button.text for button in first_buttons]


def _table(game_key, count):
    state = table_rules.new(game_key=game_key, chat_id=-100, owner_id=1,
                            owner_name="<Bir>&", now=100)
    for user_id in range(2, count + 1):
        state = table_rules.join(state, user_id=user_id,
                                 user_name=f"Oyuncu {user_id}", now=100)
    for user_id in range(1, count + 1):
        state = table_rules.ready(state, user_id=user_id)
    return table_rules.start(state, user_id=1, now=101, seed=3)


def test_pisti_panel_guides_actor_and_explains_last_capture():
    state = _table("pisti", 2)
    state["phase_ready"] = True
    state["deadline"] = 160
    state["last_event"] = {"actor": 1, "card": "JH", "capture": True,
                           "pisti": False, "timeout": False}
    panel = card_table_ui.group_text(state)
    assert "&lt;Bir&gt;&amp;" in panel
    assert "Özel elindeki kartlardan birini seçip oyna" in panel
    assert "V Kupa ile masayı aldı" in panel
    assert "Masada:" in card_table_ui.private_text(state, 1)


def test_oldmaid_panel_shows_progress_without_leaking_last_card():
    state = _table("papaz_kimde", 3)
    state["phase_ready"] = True
    state["deadline"] = 160
    state["last_event"] = {"actor": 1, "target": 2,
                           "pairs_removed": 1, "timeout": False}
    state["last_draw"] = {"actor": 1, "target": 2, "card": "KS"}
    panel = card_table_ui.group_text(state)
    assert "1 çift atıldı" in panel
    assert "Papaz Maça" not in panel
    assert "Son çektiğin kart: Papaz♠" in card_table_ui.private_text(state, 1)
