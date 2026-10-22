"""Deterministic retro cards for Arel's Telegram surfaces.

Cards are generated locally with Pillow so profile, achievement and
leaderboard visuals do not spend an AI request or depend on a remote image
host.  Rendering is intentionally pure: callers provide a destination path
and the data envelope, then handle Telegram delivery and cleanup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from PIL import Image, ImageDraw, ImageFont


class ArelCardRenderer:
    SIZE = (640, 480)
    BG = (12, 23, 43)
    PANEL = (49, 68, 91)
    PANEL_DARK = (22, 35, 57)
    TEXT = (239, 245, 255)
    MUTED = (174, 190, 211)
    TEAL = (38, 207, 190)
    YELLOW = (255, 204, 40)
    GREEN = (73, 220, 124)

    def __init__(self, font_paths: Iterable[str] | None = None):
        candidates = list(font_paths or ()) + [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/consola.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
        self._font_path = next((path for path in candidates if Path(path).exists()), None)

    def _font(self, size: int, bold: bool = False):
        path = self._font_path
        if bold and path:
            sibling = str(Path(path).with_name(Path(path).stem + "-Bold" + Path(path).suffix))
            if Path(sibling).exists():
                path = sibling
        try:
            return ImageFont.truetype(path, size) if path else ImageFont.load_default()
        except OSError:
            return ImageFont.load_default()

    def _base(self, title: str, subtitle: str = ""):
        image = Image.new("RGB", self.SIZE, self.BG)
        draw = ImageDraw.Draw(image)
        # Pixel-like grid and a clean top accent make the visual consistent
        # across welcome, profile, achievement and leaderboard cards.
        for x in range(0, self.SIZE[0], 16):
            draw.line((x, 0, x, self.SIZE[1]), fill=(15, 31, 52), width=1)
        for y in range(0, self.SIZE[1], 16):
            draw.line((0, y, self.SIZE[0], y), fill=(15, 31, 52), width=1)
        draw.rectangle((0, 0, self.SIZE[0], 8), fill=self.TEAL)
        draw.ellipse((36, 42, 124, 130), fill=self.TEAL)
        draw.text((64, 59), "P", font=self._font(48, True), fill=self.BG, anchor="mm")
        draw.text((146, 48), title[:28], font=self._font(28, True), fill=self.TEXT)
        if subtitle:
            draw.text((148, 91), subtitle[:42], font=self._font(16), fill=self.MUTED)
        return image, draw

    def _panel(self, draw, box, fill=None):
        draw.rectangle(box, fill=fill or self.PANEL)

    def _bar(self, draw, box, value: float, fill):
        x1, y1, x2, y2 = box
        draw.rectangle(box, fill=self.PANEL_DARK)
        width = max(0, min(1, float(value))) * (x2 - x1)
        draw.rectangle((x1, y1, x1 + int(width), y2), fill=fill)

    def render_welcome(self, destination: str | Path, first_name: str):
        image, draw = self._base("PASTALİKEK", "AREL • TELEGRAM ASİSTANI")
        self._panel(draw, (28, 164, 612, 302))
        draw.text((50, 188), f"HOŞ GELDİN, {first_name[:24].upper()}!", font=self._font(27, True), fill=self.YELLOW)
        draw.text((50, 237), "Sohbet, medya, oyun ve üretkenlik", font=self._font(21), fill=self.TEXT)
        draw.text((50, 270), "tek bir menüde. Komut yaz; gerisini Arel düşünsün.", font=self._font(18), fill=self.MUTED)
        draw.text((28, 430), "AREL // PASTALİKEK", font=self._font(15, True), fill=self.TEAL)
        self._save(image, destination)

    def render_profile(self, destination: str | Path, stats: Mapping, display_name: str):
        level = int(stats.get("level", 1) or 1)
        xp = int(stats.get("xp", 0) or 0)
        next_xp = max(1, int(stats.get("next_xp", 100) or 100))
        coins = int(stats.get("coins", 0) or 0)
        games = int(stats.get("games", stats.get("games_played", 0)) or 0)
        wins = int(stats.get("wins", 0) or 0)
        image, draw = self._base(display_name[:22].upper(), "OYUNCU PROFİLİ")
        self._panel(draw, (28, 164, 612, 240))
        draw.text((46, 179), f"SEVİYE {level}", font=self._font(22, True), fill=self.TEXT)
        draw.text((188, 179), f"{xp}/{next_xp} XP", font=self._font(20), fill=self.TEXT)
        self._bar(draw, (46, 216, 594, 228), xp / next_xp, self.TEAL)
        self._panel(draw, (28, 258, 612, 322))
        draw.text((48, 279), f"● {coins} COIN", font=self._font(24, True), fill=self.YELLOW)
        self._panel(draw, (28, 340, 612, 410))
        draw.text((48, 355), f"OYUN {games}    GALİBİYET {wins}", font=self._font(18, True), fill=self.TEXT)
        ratio = (wins / games) if games else 0
        draw.text((48, 383), f"KAZANMA ORANI %{ratio * 100:.0f}", font=self._font(15), fill=self.MUTED)
        draw.text((28, 442), "AREL PROFİL // PASTALİKEK", font=self._font(14, True), fill=self.TEAL)
        self._save(image, destination)

    def render_leaderboard(self, destination: str | Path, rows: Iterable[Mapping], title: str = "LİDERLİK TABLOSU"):
        image, draw = self._base(title[:24], "SEZON PERFORMANSI")
        rows = list(rows)[:7]
        y = 158
        for index, row in enumerate(rows, 1):
            self._panel(draw, (28, y, 612, y + 38), fill=self.PANEL if index % 2 else self.PANEL_DARK)
            name = str(row.get("user_name") or row.get("name") or row.get("username") or "Oyuncu")[:23]
            score = row.get("score", row.get("points", row.get("total_score", 0)))
            draw.text((44, y + 9), f"{index:02d}  {name}", font=self._font(17, index <= 3), fill=self.YELLOW if index <= 3 else self.TEXT)
            draw.text((548, y + 9), str(score), font=self._font(17, True), fill=self.TEXT, anchor="ra")
            y += 43
        draw.text((28, 442), "PUANLAR OYUN KAYITLARINDAN HESAPLANIR", font=self._font(13), fill=self.MUTED)
        self._save(image, destination)

    def render_achievements(self, destination: str | Path, rows: Iterable[Mapping], display_name: str):
        image, draw = self._base(display_name[:22].upper(), "BAŞARIMLAR")
        rows = list(rows)[:7]
        y = 164
        for row in rows:
            title = str(row.get("title") or row.get("name") or row.get("achievement") or "Başarım")[:31]
            detail = str(row.get("description") or row.get("text") or "Tamamlandı")[:43]
            self._panel(draw, (28, y, 612, y + 54))
            draw.text((46, y + 9), f"★ {title}", font=self._font(18, True), fill=self.YELLOW)
            draw.text((70, y + 33), detail, font=self._font(14), fill=self.MUTED)
            y += 61
        if not rows:
            draw.text((48, 194), "İlk başarımın burada görünecek.", font=self._font(20), fill=self.TEXT)
        draw.text((28, 442), "AREL BAŞARIM ARŞİVİ", font=self._font(14, True), fill=self.TEAL)
        self._save(image, destination)

    @staticmethod
    def _save(image: Image.Image, destination: str | Path):
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="PNG", optimize=False)

