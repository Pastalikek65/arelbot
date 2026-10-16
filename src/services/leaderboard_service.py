"""Event-idempotent game scores with seasonal progression and achievements."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4


class LeaderboardService:
    def __init__(self, db, card_renderer=None):
        self.db = db
        self.card_renderer = card_renderer

    async def render_leaderboard_card(self, destination, chat_id: int, game_type: Optional[str] = None, limit: int = 10) -> bool:
        if not self.card_renderer:
            return False
        rows = await self.db.get_leaderboard(chat_id=chat_id, game_type=game_type, limit=limit)
        if not rows:
            return False
        import asyncio
        await asyncio.to_thread(self.card_renderer.render_leaderboard, destination, rows, "LÄ°DERLÄ°K TABLOSU")
        return True

    async def render_profile_card(self, destination, user_id: int, fallback_name: str = "Oyuncu") -> bool:
        if not self.card_renderer:
            return False
        stats = await self.db.get_player_stats(user_id)
        if not stats:
            return False
        import asyncio
        await asyncio.to_thread(self.card_renderer.render_profile, destination, stats, stats.get("user_name") or fallback_name)
        return True

    async def render_achievements_card(self, destination, user_id: int, chat_id: int) -> bool:
        if not self.card_renderer:
            return False
        getter = getattr(self.db, "get_game_achievements", None)
        rows = await getter(int(user_id), int(chat_id), limit=50) if getter else []
        import asyncio
        await asyncio.to_thread(self.card_renderer.render_achievements, destination, rows, "Arel")
        return bool(rows)

    @staticmethod
    def current_season(now: datetime | None = None) -> str:
        value = now or datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        quarter = ((value.month - 1) // 3) + 1
        return f"{value.year}-Q{quarter}"

    @staticmethod
    def _achievements(game_type: str, score: int) -> list[str]:
        result = ["first_game"]
        if score >= 10:
            result.append("perfect_round")
        if game_type == "quiz":
            result.append("quiz_player")
        return result

    async def _record_progress(
        self, user_id: int, chat_id: int, game_type: str, score: int,
        user_name: str | None, event_id: str
    ) -> None:
        season_id = self.current_season()
        achievements = self._achievements(game_type, score)
        recorder = getattr(self.db, "record_game_progress", None)
        if recorder is not None:
            await recorder(
                event_id=event_id,
                user_id=int(user_id), chat_id=int(chat_id), season_id=season_id,
                points=max(0, int(score)), game_type=str(game_type),
                achievements=achievements, user_name=user_name,
            )
            return
        # Lightweight fake databases can test score idempotency without a
        # durable progression repository. Production Database always exposes it.

    async def record_score(
        self,
        user_id: int,
        chat_id: int,
        game_type: str,
        score: int,
        user_name: str | None = None,
        event_id: str | None = None,
    ) -> bool:
        score = int(score)
        if score < -100 or score > 100:
            raise ValueError("score_out_of_range")
        progression_event_id = str(event_id or uuid4().hex)
        accepted = await self.db.save_game_score(
            user_id=user_id, chat_id=chat_id, game_type=game_type,
            score=score, user_name=user_name, event_id=event_id,
        )
        if accepted is False:
            recorder = getattr(self.db, "record_game_progress", None)
            if recorder is not None:
                await self._record_progress(
                    user_id, chat_id, game_type, score, user_name, progression_event_id
                )
            return False
        await self.db.update_player_stats(user_id=user_id, score_delta=score, user_name=user_name)
        await self._record_progress(user_id, chat_id, game_type, score, user_name, progression_event_id)
        return True

    async def render_leaderboard(self, chat_id: int, game_type: Optional[str] = None, limit: int = 10) -> str:
        rows = await self.db.get_leaderboard(chat_id=chat_id, game_type=game_type, limit=limit)
        if not rows:
            return "ğŸ† Bu sohbet iÃ§in henÃ¼z liderlik kaydÄ± yok. Oyun oynayÄ±p puan kazanmaya baÅŸla!"
        title = "ğŸ† <b>LÄ°DERLÄ°K TABLOSU</b>"
        if game_type:
            title += f" â€” <b>{game_type.upper()}</b>"
        lines = [title, ""]
        medals = ["ğŸ¥‡", "ğŸ¥ˆ", "ğŸ¥‰"]
        for index, row in enumerate(rows, 1):
            medal = medals[index - 1] if index <= 3 else f"{index}."
            name = row.get("user_name") or f"ID:{row.get('_id')}"
            score = row.get("total_score", 0)
            games = row.get("games_played", 0)
            lines.append(f"{medal} <b>{name}</b> â€” <code>{score}</code> puan ({games} oyun)")
        return "\n".join(lines)

    async def render_profile(self, user_id: int, fallback_name: str = "Oyuncu") -> str:
        stats = await self.db.get_player_stats(user_id)
        if not stats:
            return f"ğŸ‘¤ <b>{fallback_name}</b> iÃ§in henÃ¼z oyun istatistiÄŸi yok."
        return (
            f"ğŸ‘¤ <b>{stats.get('user_name') or fallback_name}</b>\n\n"
            f"ğŸ† Toplam puan: <code>{stats.get('total_score', 0)}</code>\n"
            f"ğŸ® Oynanan oyun: <code>{stats.get('games_played', 0)}</code>"
        )

    async def render_achievements(self, user_id: int, chat_id: int) -> str:
        getter = getattr(self.db, "get_game_achievements", None)
        rows = await getter(int(user_id), int(chat_id), limit=50) if getter else []
        if not rows:
            return "ğŸ… HenÃ¼z bir baÅŸarÄ±m kazanmadÄ±n. Bir oyun baÅŸlat; tablo seni bekliyor."
        lines = ["ğŸ… <b>BAÅARIMLARIN</b>", ""]
        lines.extend(f"âœ… {str(row.get('label') or row.get('key') or 'BaÅŸarÄ±m')}" for row in rows[:50])
        return "\n".join(lines)

    async def render_season_leaderboard(
        self, chat_id: int, *, now: datetime | None = None, limit: int = 10
    ) -> str:
        season_id = self.current_season(now)
        getter = getattr(self.db, "get_season_leaderboard", None)
        rows = await getter(int(chat_id), season_id, limit=limit) if getter else []
        if not rows:
            return f"ğŸ† <b>{season_id} sezonu</b>\n\nHenÃ¼z puan bulunmuyor."
        lines = [f"ğŸ† <b>{season_id} SEZON LÄ°DERLÄ°ÄÄ°</b>", ""]
        for index, row in enumerate(rows[:limit], 1):
            name = row.get("user_name") or f"ID:{row.get('user_id')}"
            lines.append(f"{index}. <b>{name}</b> â€” <code>{int(row.get('points', 0))}</code> puan")
        return "\n".join(lines)
