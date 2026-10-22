"""Low-latency group moderation policy engine."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit
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
        configured = str(settings.get("flood_mode") or "").casefold()
        if configured:
            if configured in {"delete", "mute"}:
                return configured
            if configured in {"ban", "tban", "kick"}:
                return "ban"
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
        guardian_policy_keys = {
            "flood_limit", "link_filter", "antibotlink_enabled", "blocked_words",
            "guardian_filters", "repeat_filter", "moderation_enabled",
        }
        policy_enabled = bool(settings.get("moderation_enabled")) or any(
            key in settings and settings.get(key) not in (None, False, [], {}, "")
            for key in guardian_policy_keys
        )
        approved = {str(item) for item in (settings.get("approved_users") or [])}
        approved.update(str(item) for item in (settings.get("guardian_federation_approved") or []))
        roles = settings.get("guardian_roles")
        role = str(roles.get(str(user_id), "")).casefold() if isinstance(roles, dict) else ""
        if is_admin or str(user_id) in approved or role == "free" or not policy_enabled:
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

        link_policy = bool(settings.get("link_filter"))
        bot_link_policy = bool(settings.get("antibotlink_enabled")) and bool(settings.get("_sender_is_bot"))
        if (link_policy or bot_link_policy) and self.URL_RE.search(text or ""):
            allowed = {str(item).casefold().lstrip("@.") for item in (settings.get("allowed_domains") or []) if str(item).strip()}
            urls = re.findall(r"https?://[^\s<>]+", text or "", flags=re.IGNORECASE)
            blocked = False
            for raw_url in urls or [""]:
                host = (urlsplit(raw_url).hostname or "").casefold().lstrip("www.")
                if host and any(host == item or host.endswith("." + item) for item in allowed):
                    continue
                blocked = True
                break
            if blocked:
                return ModerationDecision(False, "bot_link_filter" if bot_link_policy and not link_policy else "link_filter", "delete")

        blocked = {
            self._normalize(str(word))
            for word in (settings.get("blocked_words") or settings.get("guardian_filters") or [])
            if word
        }
        normalized_words = {
            self._normalize(word) for word in re.findall(r"\w+", text or "", flags=re.UNICODE)
        }
        blocklist_enabled = settings.get("blocklist_mode_enabled", True)
        if blocked and bool(blocklist_enabled) and any(word in normalized_words for word in blocked):
            mode = str(settings.get("blocklist_mode") or "delete").casefold()
            action = mode if mode in {"delete", "mute", "ban"} else "delete"
            return ModerationDecision(False, "keyword_filter", action)

        digest = hashlib.sha256((text or "").strip().casefold().encode()).hexdigest()
        repeats = self._recent_hashes[key]
        while repeats and now - repeats[0][1] > self.window_seconds:
            repeats.popleft()
        repeats.append((digest, now))
        if settings.get("repeat_filter") and sum(1 for item, _ in repeats if item == digest) >= 3:
            return ModerationDecision(False, "repeat_filter", "delete")
        return ModerationDecision(True)
