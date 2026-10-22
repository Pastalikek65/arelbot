"""Group output never exposes a narrator's private card or invitation."""

from src.games import tabu
from src.services.tabu_ui import render_tabu, tabu_keyboard, render_private_card


def test_group_panel_and_callback_payloads_do_not_leak_card_or_seed():
    state = tabu.new_lobby(chat_id=-1004323938202, thread_id=12345, owner_id=1, owner_name="<A>", round_id="123456abcdef", deck_seed=2**127)
    for uid, team in [(2, "blue"), (3, "red"), (4, "blue")]:
        state = tabu.join(state, user_id=uid, user_name=str(uid), team=team)
    for uid in range(1, 5):
        state = tabu.set_ready(state, user_id=uid)
    state = tabu.start(state, user_id=1, now=10)
    card = tabu.private_card(state, user_id=1, card_id=state["card_id"])
    panel = render_tabu(state, now=10)
    assert card["target"] not in panel
    assert all(word not in panel for word in card["forbidden"])
    assert "&lt;A&gt;" in panel and "<A>" not in panel
    for row in tabu_keyboard(state).inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode()) <= 64
            assert card["id"] not in button.callback_data
    assert card["target"] in render_private_card(card)


def test_tabu_is_registered_as_a_distinct_team_game():
    from src.games.catalog import get_game_definition
    from src.services.menu_service import MenuService
    definition = get_game_definition("tabu")
    assert definition.min_players == 4 and definition.max_players == 20
    _, home = MenuService().games_menu()
    assert any(button.callback_data == "menu_games:social" for row in home.inline_keyboard for button in row)
    _, keyboard = MenuService().games_menu(category="social")
    assert any(button.callback_data == "game_start_tabu" for row in keyboard.inline_keyboard for button in row)
