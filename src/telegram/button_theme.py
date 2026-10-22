"""Pure inline-button presentation; game actions and answer labels stay intact."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import cache
from pathlib import Path
from types import MappingProxyType

from src.telegram.chess_theme import decorate_chess_square

GAME_ICONS = MappingProxyType(
    {
        "word_game": "🔤",
        "word_guess": "🎭",
        "hangman": "🔠",
        "connect4": "🔴",
        "gomoku": "⚫",
        "chess": "♟️",
        "akinator": "🔮",
        "guess_age": "🎂",
        "uno": "🃏",
        "kelime_zinciri": "🔗",
        "tabu": "🤫",
        "casus": "🕵️",
        "vampir_koylu": "🧛",
        "tictactoe": "⭕",
        "quiz": "💡",
        "wordle_tr": "🟩",
        "batak": "♠️",
        "blackjack": "🃏",
        "bottle": "🍾",
        "karsi_kelime": "🔄",
        "anagram": "🔀",
        "hafiza_eslestirme": "🧠",
        "mayin_tarlasi": "💣",
        "sudoku": "🔢",
        "sayisal_tahmin": "🎯",
        "hizli_buton": "⚡️",
        "harf_maratonu": "🔤",
        "sozcuk_avi": "🔎",
        "yasak_harf": "🤐",
        "ortak_hikaye": "✍️",
        "bomba": "💣",
        "kor_siralama": "📊",
        "kronoloji": "📆",
        "dogru_yanlis": "⚖️",
        "dedektif": "🔎",
        "amiral_batti": "🚢",
        "iki_dogru_bir_yalan": "🎭",
        "kimim_ben": "❓",
        "takim_bilgi_ligi": "🏆",
        "zehirli_kadeh": "🍷",
        "pisti": "🃏",
        "papaz_kimde": "👑",
        "2048": "🔢",
        "bes_ipucu": "💡",
        "emoji_bilmece": "🧩",
    }
)
_ALIASES = MappingProxyType(
    {
        "anlat": "word_guess",
        "wordgame": "word_game",
        "sise": "bottle",
        "chain": "kelime_zinciri",
        "vampire": "vampir_koylu",
    }
)
_LABELS = MappingProxyType(
    {
        "katil": ("✅", "success"),
        "baslat": ("⚡️", "success"),
        "basla": ("⚡️", "success"),
        "hazirim": ("✅", "success"),
        "karti aldim": ("✅", "success"),
        "oyunu bitir": ("🏁", "danger"),
        "bitir": ("🏁", "danger"),
        "oyunu iptal et": ("🏁", "danger"),
        "iptal": ("🏁", "danger"),
        "ayril": ("🚪", None),
        "masadan ayril": ("🚪", None),
        "ana menu": ("🏠", None),
        "oyun salonu": ("🎮", "primary"),
        "yeniden oyna": ("🎮", "success"),
        "tekrar oyna": ("🎮", "success"),
        "ozel sohbeti hazirla": ("🔐", None),
        "gizli kartim": ("🔐", None),
        "ozel sohbet": ("🔐", None),
        "kartimi goster": ("🔐", None),
        "yenile": ("🔄", None),
        "durum": ("🔄", None),
        "pas": ("⏭️", None),
        "ipucu": ("💡", None),
        "nasil oynanir?": ("💡", None),
        "liderlik tablosu": ("🏆", None),
        "liderlik": ("🏆", None),
        "kart oyunlari": ("🃏", None),
        "kelime oyunlari": ("🔤", None),
        "tahta oyunlari": ("♟️", None),
        "tahmin ve bilgi": ("💡", None),
        "tum oyun kategorileri": ("🎮", None),
        "kopyala": ("📋", None),
        "paylas": ("📣", None),
        "geri": ("⬅️", None),
        "risk 1": ("🛡️", None),
        "risk 2": ("🔥", None),
        "cuzdan ve magaza": ("🪙", None),
        "cuzdan": ("🪙", None),
        "magaza": ("🛍️", None),
        "envanter": ("🎒", None),
        "satin al": ("🛍️", "success"),
        "gunluk odul": ("🎁", None),
    }
)


def _key(text):
    return str(text).casefold().translate(str.maketrans("ıöüçşğ", "ioucsg"))


def _plain(text):
    """Strip only a decorative prefix, never change callback or game inputs."""
    index = 0
    while index < len(text) and not text[index].isalnum():
        index += 1
    return text[index:].strip()


def _emoji_key(value):
    return value.replace("\ufe0f", "")


@cache
def custom_icons():
    path = Path(__file__).resolve().parents[2] / "assets/telegram/button_icons.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != 1:
        raise ValueError("button_icon_catalog_schema")
    icons = {}
    for item in catalog["icons"]:
        identifier = item["custom_emoji_id"]
        if (
            not isinstance(identifier, str)
            or not identifier.isascii()
            or not identifier.isdecimal()
            or not 0 < int(identifier) < 2**64
        ):
            raise ValueError("button_icon_catalog_identifier")
        icons[_emoji_key(item["emoji"])] = identifier
    return MappingProxyType(icons)


# Load immutable application metadata during module startup, before the
# bot/worker event loop serves updates. Request formatting only reads cache.
custom_icons()


def _intent(button):
    text = button.get("text", "")
    if not isinstance(text, str):
        return None
    plain = _plain(text)
    if not plain:
        return None
    action = button.get("callback_data")
    if isinstance(action, str) and action.startswith("game_start_"):
        key = action.removeprefix("game_start_")
        icon = GAME_ICONS.get(_ALIASES.get(key, key))
        if icon:
            return icon, None, plain
    if plain.isdecimal():
        return None
    intent = _LABELS.get(_key(plain))
    if intent:
        return *intent, plain
    if isinstance(action, str) and action.startswith("menu_games:social"):
        return "🎭", None, plain
    if _key(plain).startswith(("yardim", "oyun yardimina", "komut rehberi")):
        return "💡", None, plain
    if _key(plain).startswith(("onceki", "konulara don")):
        return "⬅️", None, plain
    if _key(plain).startswith(("sonraki", "diger oyunlar")):
        return "➡️", None, plain
    if _key(plain).startswith(("ek ipucu", "ipucu (")):
        return "💡", None, plain
    if isinstance(action, str):
        menu_icons = {
            "menu_main": "🏠",
            "menu_games": "🎮",
            "menu_leaderboard": "🏆",
            "menu_media": "🎨",
            "menu_ai": "🧠",
            "menu_plan": "📝",
            "menu_settings": "⚙️",
            "menu_tools": "🧰",
            "menu_language": "🌐",
            "menu_reminders": "🔔",
            "game_word_categories": "🔤",
            "game_word_difficulty": "🎚️",
            "game_hangman_difficulty": "🔠",
        }
        if action in menu_icons:
            return menu_icons[action], None, plain
        for prefix, icon in (
            ("ayar_", "⚙️"),
            ("settings_", "⚙️"),
            ("help_", "💡"),
            ("news_", "📰"),
            ("notify_", "🔔"),
            ("plan_", "📝"),
            ("daily_brief", "📆"),
        ):
            if action.startswith(prefix) and plain == text:
                return icon, None, plain
    # Upgrade an existing icon only when it is in the verified Telegram set.
    prefix = text[: len(text) - len(text.lstrip())] if plain == text else text[: text.find(plain)].strip()
    if _emoji_key(prefix) in custom_icons():
        return prefix, None, plain
    return None


def decorate_markup(markup, *, premium=True):
    if not isinstance(markup, dict) or not isinstance(markup.get("inline_keyboard"), list):
        return markup
    result = deepcopy(markup)
    for row in result["inline_keyboard"]:
        for button in row:
            if not isinstance(button, dict) or button.get("icon_custom_emoji_id"):
                continue
            if decorate_chess_square(button, premium=premium):
                continue
            intent = _intent(button)
            if intent is None:
                continue
            icon, style, plain = intent
            identifier = custom_icons().get(_emoji_key(icon)) if premium else None
            button["text"] = plain if identifier else f"{icon} {plain}"
            if identifier:
                button["icon_custom_emoji_id"] = identifier
            if style and "style" not in button:
                button["style"] = style
    return result
