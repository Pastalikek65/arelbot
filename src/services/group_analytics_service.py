"""Repository-backed group analytics and safe Telegram rendering."""

from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from typing import Any


class GroupAnalyticsService:
    def __init__(self, database: Any) -> None:
        self.database = database

    async def summary(self, chat_id: int, days: int = 7) -> dict[str, Any]:
        try:
            days = int(days)
        except (TypeError, ValueError) as exc:
            raise ValueError("days_out_of_range") from exc
        if days < 1 or days > 90:
            raise ValueError("days_out_of_range")
        if not isinstance(chat_id, int) or chat_id >= 0:
            raise ValueError("group_chat_required")
        since = datetime.now(timezone.utc) - timedelta(days=days)
        result = await self.database.get_group_analytics(chat_id, since=since, limit=5)
        return {
            "chat_id": chat_id,
            "days": days,
            "since": since,
            "total_messages": int(result.get("total_messages", 0) or 0),
            "active_users": int(result.get("active_users", 0) or 0),
            "peak_hour": result.get("peak_hour"),
            "top_users": list(result.get("top_users") or [])[:5],
        }

    async def render(self, chat_id: int, days: int = 7) -> str:
        data = await self.summary(chat_id, days)
        title = "HAFTALIK GRUP ANALİZİ" if data["days"] == 7 else f"{data['days']} GÜNLÜK GRUP ANALİZİ"
        lines = [f"📊 <b>{title}</b>", "", f"💬 Mesaj: <b>{data['total_messages']}</b>"]
        lines.append(f"👥 Aktif kullanıcı: <b>{data['active_users']}</b>")
        peak_hour = data.get("peak_hour")
        if peak_hour is not None:
            hour = max(0, min(23, int(peak_hour)))
            lines.append(f"🕘 En yoğun saat: <b>{hour:02d}:00–{(hour + 1) % 24:02d}:00</b>")
        top_users = data.get("top_users") or []
        if top_users:
            lines.extend(["", "🏆 <b>En aktif üyeler</b>"])
            for index, user in enumerate(top_users, 1):
                name = html.escape(str(user.get("name") or "Bilinmeyen"))[:80]
                count = max(0, int(user.get("count", 0) or 0))
                lines.append(f"{index}. {name} · <b>{count}</b> mesaj")
        lines.extend(["", "<i>İstatistikler yalnızca son dönemdeki botun kaydettiği grup mesajlarından hesaplanır.</i>"])
        return "\n".join(lines)

