"""Low-latency group moderation policy engine."""

from __future__ import annotations

import hashlib
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class ModerationDecision:
    allowed: bool
    reason: str | None = None
    action: str = "none"
    strike_count: int = 0
    persistent: bool = False


class ModerationEngine:
    URL_RE = re.compile(r"https?://", re.IGNORECASE)

    def __init__(self, window_seconds: int = 10):
        self.window_seconds = window_seconds
        self._events: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self._recent_hashes: dict[tuple[int, int], deque[tuple[str, float]]] = defaultdict(deque)
        self._violations: dict[tuple[int, int], deque[float]] = defaultdict(deque)

    def _flood_action(self, key: tuple[int, int], settings: dict, now: float) -> str:
        violations = self._violations[key]
        retention = max(300, self.window_seconds * 60)
        while violations and now - violations[0] > retention:
            violations.popleft()
        violations.append(now)
        if settings.get("auto_ban") and len(violations) >= 3:
            return "ban"
        return "mute"

    @staticmethod
    def _normalize(value: str) -> str:
        # casefold() represents capital I-with-dot as ``i`` plus a combining
        # dot. Removing that mark gives stable Turkish word matching.
        return (value or "").casefold().replace("\u0131", "i").replace("\u0307", "")

    def evaluate(self, chat_id: int, user_id: int, text: str, settings: dict | None = None,
                 is_admin: bool = False) -> ModerationDecision:
        settings = settings or {}
        if is_admin or settings.get("moderation_enabled", False) is False:
            return ModerationDecision(True)
        now = time.monotonic()
        key = (chat_id, user_id)
        events = self._events[key]
        while events and now - events[0] > self.window_seconds:
            events.popleft()
        events.append(now)
        flood_limit = int(settings.get("flood_limit", 8))
        if len(events) > flood_limit:
            return ModerationDecision(False, "flood", self._flood_action(key, settings, now))

        if settings.get("link_filter") and self.URL_RE.search(text or ""):
            return ModerationDecision(False, "link_filter", "delete")

        blocked = {self._normalize(str(word)) for word in settings.get("blocked_words", []) if word}
        normalized_words = {
            self._normalize(word) for word in re.findall(r"\w+", text or "", flags=re.UNICODE)
        }
        if any(word in normalized_words for word in blocked):
            return ModerationDecision(False, "keyword_filter", "delete")

        digest = hashlib.sha256((text or "").strip().casefold().encode()).hexdigest()
        repeats = self._recent_hashes[key]
        while repeats and now - repeats[0][1] > self.window_seconds:
            repeats.popleft()
        repeats.append((digest, now))
        if settings.get("repeat_filter") and sum(1 for item, _ in repeats if item == digest) >= 3:
            return ModerationDecision(False, "repeat_filter", "delete")
        return ModerationDecision(True)
