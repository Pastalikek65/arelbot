"""Named quote themes with safe, validated color palettes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuoteTheme:
    name: str
    bubble: str
    name_color: tuple[int, int, int, int]
    text_color: tuple[int, int, int, int]
    canvas: tuple[int, int, int, int] = (0, 0, 0, 0)


THEMES = {
    "dark": QuoteTheme("dark", "#171723", (130, 200, 255, 255), (255, 255, 255, 255)),
    "light": QuoteTheme("light", "#f5f7fb", (45, 92, 160, 255), (30, 35, 45, 255)),
    "telegram": QuoteTheme("telegram", "#17212b", (100, 180, 240, 255), (235, 245, 250, 255)),
    "whatsapp": QuoteTheme("whatsapp", "#075e54", (190, 245, 190, 255), (255, 255, 255, 255)),
    "discord": QuoteTheme("discord", "#313338", (160, 170, 255, 255), (245, 245, 250, 255)),
    "instagram": QuoteTheme("instagram", "#4a1942", (255, 170, 220, 255), (255, 245, 255, 255)),
}


def get_theme(name: str | None) -> QuoteTheme:
    return THEMES.get((name or "dark").strip().lower(), THEMES["dark"])
