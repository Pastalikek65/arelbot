from copy import deepcopy
from io import BytesIO

from PIL import Image

from src.services.blackjack_visual import render_blackjack_table, visible_cards


def example():
    return {"terminal": False, "state": {
        "player_hand": [["A", "♠️"], ["10", "♥️"]],
        "dealer_hand": [["K", "♣️"], ["Q", "♦️"]],
        "deck": [["2", "♠️"]],
    }}


def test_hidden_dealer_and_deck_do_not_affect_public_image():
    first = example()
    second = deepcopy(first)
    # Invalid secret values deliberately prove they are never parsed.
    second["state"]["dealer_hand"][1] = ["SECRET", "SECRET"]
    second["state"]["deck"] = object()
    assert visible_cards(second) == (["spades_A", "hearts_10"], ["clubs_K", "back"])
    assert render_blackjack_table(first).getvalue() == render_blackjack_table(second).getvalue()


def test_terminal_reveals_faces_and_image_is_valid():
    result = example()
    result["terminal"] = True
    assert visible_cards(result)[1] == ["clubs_K", "diamonds_Q"]
    image = Image.open(BytesIO(render_blackjack_table(result).getvalue()))
    assert image.width == 400
    assert image.height > 400


def test_long_hand_wraps_without_clipping():
    result = example()
    result["state"]["player_hand"] *= 8
    image = Image.open(render_blackjack_table(result))
    assert image.height > 650
