#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Telegram bot to play UNO in group chats
# Copyright (c) 2016 Jannes Höke <uno@jhoeke.de>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""Arel adaptation of maildian/uno-telegram-bot results.py (01640d1).

The upstream ``add_choose_color``, ``add_draw``, ``add_gameinfo``,
``add_pass``, ``add_call_bluff`` and ``add_card`` result shapes are retained.
State and action IDs are supplied by Arel's adapter; sticker IDs are supplied
per bot by the caller after uploading the unmodified upstream PNGs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from telegram import InlineQueryResultArticle, InlineQueryResultCachedSticker as Sticker
from telegram import InputTextMessageContent


COLOR_NAMES = {"red": "Kırmızı", "blue": "Mavi", "green": "Yeşil", "yellow": "Sarı"}
KIND_NAMES = {"draw2": "+2", "draw4": "+4", "wild": "Joker",
              "skip": "Pas", "reverse": "Yön değiştir"}


def game_info(state: Mapping[str, Any]) -> InputTextMessageContent:
    """Upstream's disabled-card/game-info text, using only public table data."""
    players = state.get("players") or []
    actor = next((p for p in players if p.get("user_id") == state.get("current_player_id")), None)
    top = (state.get("discard") or [{}])[-1]
    active_color = state.get("current_color") if top.get("color") == "black" else top.get("color")
    color_label = COLOR_NAMES.get(active_color, "Siyah")
    top_label = f"{color_label} {KIND_NAMES.get(top.get('kind'), top.get('kind', '—'))}"
    roster = " → ".join(f"{str(p.get('name') or 'Oyuncu')[:20]} ({len(p.get('hand') or [])} kart)"
                        for p in players)
    return InputTextMessageContent(
        f"Sıra: {str((actor or {}).get('name') or 'Oyuncu')[:20]}\n"
        f"Üst kart: {top_label}\nOyuncular: {roster}"
    )


def add_choose_color(results: list, state: Mapping[str, Any], encode_action: Callable) -> None:
    """Add choose-color articles as in upstream results.py."""
    for color, label in COLOR_NAMES.items():
        results.append(InlineQueryResultArticle(
            id=encode_action("color", color), title="Renk seç", description=label,
            input_message_content=InputTextMessageContent(f"Renk seçildi: {label}"),
        ))


def add_draw(results: list, state: Mapping[str, Any], file_ids: Mapping[str, str],
             encode_action: Callable) -> None:
    """Add upstream option_draw sticker when the actor may draw."""
    amount = int(state.get("pending_draw", 0) or 0) or 1
    results.append(Sticker(
        id=encode_action("draw", None), sticker_file_id=file_ids["option_draw"],
        input_message_content=InputTextMessageContent(f"{amount} kart çek"),
    ))


def add_gameinfo(results: list, state: Mapping[str, Any], file_ids: Mapping[str, str],
                 encode_action: Callable) -> None:
    results.append(Sticker(
        id=encode_action("noop", "info"), sticker_file_id=file_ids["option_info"],
        input_message_content=game_info(state),
    ))


def add_pass(results: list, state: Mapping[str, Any], file_ids: Mapping[str, str],
             encode_action: Callable) -> None:
    results.append(Sticker(
        id=encode_action("pass", None), sticker_file_id=file_ids["option_pass"],
        input_message_content=InputTextMessageContent("Pas geç"),
    ))


def add_call_bluff(results: list, state: Mapping[str, Any], file_ids: Mapping[str, str],
                   encode_action: Callable) -> None:
    results.append(Sticker(
        id=encode_action("bluff", None), sticker_file_id=file_ids["option_bluff"],
        input_message_content=InputTextMessageContent("+4 blöfüne itiraz et"),
    ))


def add_card(results: list, state: Mapping[str, Any], card: Mapping[str, Any],
             key: str, can_play: bool, file_ids: Mapping[str, str],
             encode_action: Callable) -> None:
    """Use upstream colored sticker or low-saturation sticker with no-op text."""
    card_id = str(card["id"])
    if can_play:
        results.append(Sticker(
            id=encode_action("play", card_id),
            sticker_file_id=file_ids[f"colored:{key}"],
        ))
    else:
        results.append(Sticker(
            id=encode_action("noop", card_id),
            sticker_file_id=file_ids[f"grey:{key}"],
            input_message_content=game_info(state),
        ))
