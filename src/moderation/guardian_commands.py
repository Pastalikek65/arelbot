"""Kek Guardian command vocabulary and typed action construction.

The Telegram bot is the only transport.  This module keeps Guardian's command
names and moderation action mapping in one place so Arel does not grow a
second, competing moderation vocabulary.  Telegram calls still go through
``TelegramModerationActionExecutor`` in the handler boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import ModerationAction


@dataclass(frozen=True, slots=True)
class GuardianCommandResult:
    command: str
    action: str | None = None
    requires_target: bool = False
    requires_message: bool = False
    duration_seconds: int | None = None


class GuardianCommandService:
    """Normalize Guardian commands without guessing from free-form text."""

    _ALIASES = {
        "sustur": "mute",
        "sus": "mute",
        "susturac": "unmute",
        "sesiac": "unmute",
        "yasakla": "ban",
        "yasagiac": "unban",
        "tekme": "kick",
        "geciciyasa": "tban",
        "tban": "tban",
        "gecicisustur": "tmute",
        "uyar": "warn",
        "uyarlar": "warns",
        "uyarisifirla": "resetwarn",
        "mesajisil": "del",
        "chatkurallari": "rules",
        "temizle": "purge",
        "dil": "language",
        "fed_olustur": "newfed",
        "fed_katil": "joinfed",
        "unfban": "funban",
        "mesajtemizle": "purge",
        "sabitle": "pin",
        "sabitlekaldir": "unpin",
        "grupkurallari": "rules",
        "kurallar": "rules",
        "yasaklikelimeler": "blocklist",
        "yasakkelime": "blocklist",
        "iznilinkler": "whitelist",
        "guvenlilink": "whitelist",
        "floodlimiti": "setflood",
        "floodcezasi": "setfloodmode",
        "agkur": "newfed",
        "agaekle": "joinfed",
        "agdanayril": "leavefed",
        "agdurum": "fedstat",
        "agban": "fban",
        "agbanac": "funban",
        "agbanlar": "fedbanlist",
        "golgeban": "sban",
        "golgeac": "unsban",
        "riskraporu": "analytics",
        # Guardian's silent action vocabulary is canonical in Arel as well.
        "sban": "sban",
        "skick": "skick",
        "smute": "smute",
        "swarn": "swarn",
        "floodlimit": "setflood",
        "floodmod": "setfloodmode",
        "yasakkelime": "addblock",
        "guvenlilink": "whitelist",
        "joinraidac": "antiraid",
        "raidbitti": "antiraid",
        "hayaletban": "sban",
        "shadowban": "sban",
        "unshadowban": "unsban",
        "fedinfo": "fedinfo",
        "myfed": "myfed",
        "delfed": "delfed",
        "kurulum": "hizlikur",
        "logkanal": "kayitkanali",
        "adminler": "yoneticiler",
        "yetkiler": "yetkipaneli",
        "not_ekle": "notekle",
        "not_sil": "notsil",
        # Arel's Turkish command surface.  Keep these aliases in the typed
        # vocabulary so the surface and the Telegram action fallback cannot
        # disagree about whether a command exists.
        "yetkiver": "admin",
        "yetkial": "deadmin",
        "yetkipaneli": "yetkiler",
        "hayalet": "hayaletban",
        "rapor": "report",
        "linkkoruma": "antilink",
        "reklamkoruma": "antibotlink",
        "raidkoruma": "antiraid",
        "dogrulama": "captcha",
        "hosgeldin": "welcome",
        "veda": "goodbye",
        "gecemodu": "nightmode",
        "grupdili": "language",
        "filtre": "filter",
        "filtreler": "filters",
        "filtresil": "stop",
        "risk": "whois",
        "guvenliliste": "fedapproved",
    }

    _ACTION_COMMANDS = {
        "mute": ("mute", True, False),
        "unmute": ("unmute", True, False),
        "ban": ("ban", True, False),
        "unban": ("unban", True, False),
        "kick": ("kick", True, False),
        "tmute": ("temp_mute", True, False),
        "tban": ("temp_ban", True, False),
        "del": ("delete_message", False, True),
        "purge": ("delete_message", False, True),
        "pin": ("pin", False, True),
        "unpin": ("unpin", False, True),
        "warn": ("warn", True, False),
        "promote": ("promote", True, False),
        "demote": ("demote", True, False),
        # Federation commands use the same typed Telegram action in the
        # current single-bot deployment.  The persisted federation id and ban
        # ledger are handled by the command service; no second Telegram bot is
        # required.
        "fban": ("ban", True, False),
        "funban": ("unban", True, False),
        "sban": ("ban", True, False),
        "skick": ("kick", True, False),
        "smute": ("mute", True, False),
        "swarn": ("warn", True, False),
        # Guardian exposes an explicit admin-targeting variant.  The handler
        # restricts it to owner/sudo actors and marks the action so the shared
        # authorization boundary can permit the intended escalation.
        "adminmute": ("mute", True, False),
        "adminunmute": ("unmute", True, False),
        "ytban": ("ban", True, False),
        "unsban": ("unban", True, False),
        "fpromote": ("promote", True, False),
        "fdemote": ("demote", True, False),
    }

    _DURATION_RE = re.compile(r"^(?P<value>\d{1,5})(?P<unit>s|m|h|d)?$", re.IGNORECASE)

    @classmethod
    def canonical(cls, command: str) -> str:
        normalized = str(command or "").strip().lstrip("/").casefold().split("@", 1)[0]
        return cls._ALIASES.get(normalized, normalized)

    @classmethod
    def describe(cls, command: str) -> GuardianCommandResult:
        canonical = cls.canonical(command)
        data = cls._ACTION_COMMANDS.get(canonical)
        if data is None:
            return GuardianCommandResult(canonical)
        action, target, message = data
        duration = None
        if canonical == "tmute":
            duration = 600
        if canonical == "tban":
            duration = 3600
        return GuardianCommandResult(canonical, action, target, message, duration)

    @classmethod
    def parse_duration(cls, value: str | None, *, default: int = 600) -> int:
        if not value:
            return default
        match = cls._DURATION_RE.match(str(value).strip())
        if not match:
            return default
        amount = int(match.group("value"))
        unit = (match.group("unit") or "s").casefold()
        multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
        return max(1, min(28 * 24 * 3600, amount * multiplier))

    @classmethod
    def build_action(
        cls,
        command: str,
        *,
        chat_id: int,
        actor_id: int,
        target_user_id: int | None = None,
        target_message_id: int | None = None,
        thread_id: int | None = None,
        duration: str | None = None,
        generation_id: str | None = None,
    ) -> ModerationAction:
        info = cls.describe(command)
        if not info.action:
            raise ValueError("guardian_command_not_action")
        duration_seconds = info.duration_seconds
        if info.action in {"temp_mute", "temp_ban"}:
            duration_seconds = cls.parse_duration(duration, default=duration_seconds or 600)
        metadata = {"source": "kek_guardian_compat", "generation_id": generation_id or ""}
        if info.command in {"sban", "skick", "smute", "swarn"}:
            metadata["silent"] = True
        if info.command in {"adminmute", "adminunmute"}:
            metadata["guardian_admin_override"] = True
        return ModerationAction(
            action=info.action,
            chat_id=int(chat_id),
            actor_id=int(actor_id),
            target_user_id=(int(target_user_id) if info.requires_target and target_user_id is not None else None),
            target_message_id=(int(target_message_id) if info.requires_message and target_message_id is not None else None),
            thread_id=(int(thread_id) if thread_id is not None else None),
            duration_seconds=duration_seconds,
            reason=f"guardian:{info.command}",
            metadata=metadata,
        )


__all__ = ["GuardianCommandResult", "GuardianCommandService"]
