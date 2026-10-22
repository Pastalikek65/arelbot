"""Blackjack table composed from licensed card art, with explicit hidden cards."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from src.services.playing_card_ui import card_key


def visible_cards(result: dict) -> tuple[list[str], list[str]]:
    """Project only public faces; never inspect a hidden dealer card or deck."""
    state = result.get("state") or {}
    player = [card_key(card) for card in state.get("player_hand", [])]
    dealer_hand = state.get("dealer_hand", [])
    if result.get("terminal"):
        dealer = [card_key(card) for card in dealer_hand]
    else:
        dealer = ([card_key(dealer_hand[0])] if dealer_hand else [])
        dealer += ["back"] * max(0, len(dealer_hand) - 1)
    return player, dealer


def render_blackjack_table(result: dict, *, assets: Path | None = None) -> BytesIO:
    assets = assets or Path("assets/playing_cards/stickers")
    player, dealer = visible_cards(result)
    if not player or not dealer:
        raise ValueError("blackjack_visual_hand_missing")
    # Wrap long hands rather than overlapping or shrinking card ranks.
    columns, card_w, card_h, gap = 5, 140, 190, 16
    row_counts = [max(1, (len(cards) + columns - 1) // columns) for cards in (dealer, player)]
    width = max(400, min(columns, max(len(player), len(dealer))) * (card_w + gap) + 60)
    height = 138 + sum(row_counts) * (card_h + gap) + 58
    table = Image.new("RGB", (width, height), "#103d35")
    draw = ImageDraw.Draw(table)
    font = ImageFont.load_default(size=22)
    draw.rounded_rectangle((12, 12, width - 12, height - 12), radius=24, outline="#bda876", width=3)
    y = 32
    for label, cards, rows in zip(("KRUPIYE", "ELIN"), (dealer, player), row_counts):
        draw.text((30, y), label, fill="#f6edd8", font=font)
        y += 36
        for index, key in enumerate(cards):
            path = assets / f"{key}.png"
            with Image.open(path) as source:
                card = source.convert("RGBA")
                card = card.crop(card.getbbox())
                card.thumbnail((card_w, card_h), Image.Resampling.LANCZOS)
                x = 30 + (index % columns) * (card_w + gap)
                cy = y + (index // columns) * (card_h + gap)
                table.paste(card, (x, cy), card)
        y += rows * (card_h + gap) + 16
    output = BytesIO()
    table.save(output, format="PNG", optimize=True)
    output.seek(0)
    output.name = "arel-blackjack.png"
    return output
