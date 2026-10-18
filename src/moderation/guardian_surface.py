"""Kek Guardian feature surface embedded in Arel.

The upstream Guardian project is a Telegram application with module-level
globals and a SQLAlchemy/Telethon persistence layer.  Arel cannot safely load
those modules in-process: handlers must remain transport-only and the bot has
one Mongo repository.  This module is the compatibility/domain layer for the
complete Guardian command vocabulary.  It stores bounded, chat-scoped state
through the injected repository port and returns a response for Telegram to
render.

No Telegram client, HTTP call, scheduler or database collection is imported
here.  That keeps the port usable in replay tests and lets the application
turn features on per group without creating a second bot.
"""

from __future__ import annotations

import json
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


def _clean(value: Any, limit: int = 256) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _on(value: str | None) -> bool | None:
    value = str(value or "").casefold()
    if value in {"on", "aç", "ac", "açık", "acik", "1", "true", "yes", "evet"}:
        return True
    if value in {"off", "kapat", "kapalı", "kapali", "0", "false", "no", "hayır", "hayir"}:
        return False
    return None


@dataclass(frozen=True, slots=True)
class GuardianCommandResult:
    """Transport-neutral result returned by the embedded Guardian surface."""

    text: str
    changed: bool = False
    setting: str | None = None
    value: Any = None
    document: dict[str, Any] | None = None
    silent: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


class GuardianSurface:
    """Persist Guardian-compatible group state through Arel's data port."""

    # This is intentionally an exhaustive list of the upstream command
    # surface.  The registry imports it so a missing handler cannot silently
    # turn into a dead command during a rolling deployment.
    COMMANDS = (
        "start", "help", "info", "id", "afk", "ban", "unban", "kick", "mute", "unmute",
        "ghelp", "hizlikur", "kayitkanali", "yoneticiler", "notekle", "notsil",
        "promote", "demote", "admin", "deadmin", "muter", "unmuter", "adminler", "yetkiler",
        "yetkipaneli", "language", "unsban",
        "settitle", "setdesc", "setpic", "ytban", "tban", "tmute",
        "del", "purge", "pin", "unpin", "zombies", "reload", "echo", "sban", "skick", "smute",
        "swarn", "mod", "unmod", "free", "unfree", "staff", "recurring", "stoprecurring",
        "hayaletban", "unpinall", "banlimit", "promotelimit", "grupinfolimit", "reaksiyon",
        "joinraidac", "raidbitti", "fakekoruma", "gorselkoruma", "packengelle", "packizinver",
        "packlistesi", "gifengelle", "gifizinver", "giflistesi", "editkoruma",
        "alias", "delalias", "aliases", "warn", "resetwarn", "warns", "setwarnlimit",
        "setwarnmode", "setflood", "setfloodmode", "antilink", "antibotlink", "whitelist",
        "rmwhitelist", "antiraid", "approve", "disapprove", "approved", "addblock", "rmblock",
        "blocklist", "blocklistmode", "broadcast", "broadcastpin", "captcha", "captchatype",
        "cleanservice", "connect", "disconnect", "export", "import", "disable", "enable",
        "disabled", "fsub", "gban", "ungban", "setwelcome", "welcome", "setgoodbye", "goodbye",
        "cleanwelcome", "welcomemute", "lock", "unlock", "lockmode", "locks", "logchannel",
        "mban", "munban", "userinfo", "firstmsg", "memberlog", "nightmode", "nighttime",
        "save", "get", "clear", "notes", "report", "setrules", "rules", "privaterules", "filter",
        "stop", "filters", "newfed", "delfed", "joinfed", "leavefed", "fedinfo", "myfed",
        "fpromote", "fdemote", "fedadmins", "fedchats", "fedapproved", "fedapprove", "fedunapprove", "fedbanlist", "setappeallog", "fednotes",
        "fedtransfer", "feddelete", "setfedlog", "fnote", "unsubfed", "fedsubs", "fbanlist", "importfbans",
        "subfed", "fedstat", "fban", "funban", "gban",
        "ungban", "stats", "whois", "toprisk", "shadowstats", "analytics", "addsudo", "delsudo", "adminmute", "adminunmute",
    )

    _TOGGLE_KEYS = {
        "antilink": "link_filter",
        "antibotlink": "antibotlink_enabled",
        "antiraid": "antiraid_enabled",
        "captcha": "captcha_enabled",
        "cleanservice": "clean_service_enabled",
        "welcome": "welcome_enabled",
        "goodbye": "goodbye_enabled",
        "cleanwelcome": "clean_welcome_enabled",
        "welcomemute": "welcome_mute_enabled",
        "nightmode": "night_mode_enabled",
        "privaterules": "private_rules",
        "blocklistmode": "blocklist_mode_enabled",
    }

    _LIST_KEYS = {
        "blocklist": "blocked_words",
        "addblock": "blocked_words",
        "rmblock": "blocked_words",
        "whitelist": "allowed_domains",
        "rmwhitelist": "allowed_domains",
        "approved": "approved_users",
        "approve": "approved_users",
        "disapprove": "approved_users",
        "locks": "locked_types",
        "mban": "media_bans",
        "munban": "media_bans",
    }

    # These settings were historically accepted and written but no runtime
    # consumer read them.  Returning an explicit error is safer than telling a
    # group that protection is active when no enforcement path exists.
    _UNWIRED_FEATURES = {
        "fedapprove": "federation_trust_management",
        "fedunapprove": "federation_trust_management",
        "banlimit": "ban_limit",
        "promotelimit": "promote_limit",
        "grupinfolimit": "group_info_limit",
        "privaterules": "private_rules",
        "language": "group_language",
        "connect": "connected_chat",
        "disconnect": "connected_chat",
        "logchannel": "log_channel",
        "setappeallog": "appeal_log",
        "setfedlog": "federation_log",
        "cleanservice": "service_message_cleanup",
        "cleanwelcome": "welcome_cleanup",
        "welcomemute": "welcome_mute",
    }

    def __init__(self, data_port: Any):
        self.data_port = data_port

    @classmethod
    def _unavailable(cls, command: str) -> GuardianCommandResult:
        feature = cls._UNWIRED_FEATURES.get(command, command)
        return GuardianCommandResult(
            (
                "Federasyon genelinde güvenilir kullanıcı yönetimi bu sürümde etkin değil; ayar değiştirilmedi. "
                "Yalnızca bu grup için /approve ve /disapprove komutlarını kullanabilirsin."
                if command in {"fedapprove", "fedunapprove"}
                else "bu Guardian özelliği şu an etkin değil; ayar değişmedi"
            ),
            metadata={
                "error_code": "guardian_feature_unavailable",
                "command": command,
                "feature": feature,
            },
        )

    async def get_staff_roles(self, chat_id: int) -> dict[str, str]:
        """Return persisted Guardian staff roles for the Telegram renderer."""
        settings = await self._settings(chat_id)
        roles = settings.get("guardian_roles") or {}
        if not isinstance(roles, Mapping):
            return {}
        return {
            str(user_id): str(role)
            for user_id, role in list(roles.items())[:200]
            if str(user_id).lstrip("-").isdigit() and str(role) in {"mod", "staff", "free"}
        }

    async def _settings(self, chat_id: int) -> dict[str, Any]:
        try:
            value = await self.data_port.get_chat_settings(int(chat_id))
        except Exception:
            return {}
        return dict(value) if isinstance(value, Mapping) else {}

    async def _set(self, chat_id: int, key: str, value: Any) -> None:
        await self.data_port.update_chat_setting_key(int(chat_id), key, value)

    async def get_afk(self, user_id: int) -> dict[str, Any] | None:
        """Return a user's persisted AFK marker without exposing raw state."""
        getter = getattr(self.data_port, "get_user", None)
        if not callable(getter):
            return None
        try:
            user = await getter(int(user_id)) or {}
        except Exception:
            return None
        value = user.get("guardian_afk") if isinstance(user, Mapping) else None
        return dict(value) if isinstance(value, Mapping) else None

    async def clear_afk(self, user_id: int) -> bool:
        """Clear a user's AFK marker through the repository port."""
        updater = getattr(self.data_port, "update_user", None)
        if not callable(updater):
            return False
        try:
            await updater(int(user_id), {"guardian_afk": None})
            return True
        except Exception:
            return False

    async def get_note(self, chat_id: int, name: str) -> str | None:
        """Resolve a bounded chat note for the ``#name`` compatibility path."""
        settings = await self._settings(chat_id)
        notes = settings.get("guardian_notes") or {}
        if not isinstance(notes, Mapping):
            return None
        value = notes.get(_clean(str(name).lstrip("#").casefold(), 64))
        return _clean(value, 4000) if value is not None else None

    async def resolve_command(self, chat_id: int, command: str) -> str:
        """Resolve a bounded per-group alias chain.

        Guardian aliases are group-scoped.  Resolution is deliberately kept
        in the domain layer so command handlers, callbacks and future inline
        surfaces cannot accidentally read another chat's aliases.
        """
        current = str(command or "").strip().lstrip("/").casefold()[:64]
        seen: set[str] = set()
        for _ in range(4):
            if not current or current in seen:
                break
            seen.add(current)
            settings = await self._settings(chat_id)
            aliases = settings.get("guardian_aliases") or {}
            target = aliases.get(current) if isinstance(aliases, Mapping) else None
            if not target:
                break
            current = _clean(target, 64).lstrip("/").casefold()
        return current

    async def has_custom_alias(self, chat_id: int, command: str) -> bool:
        """Tell the catch-all command dispatcher if a group owns this alias."""
        name = str(command or "").strip().lstrip("/").casefold()[:64]
        if not name:
            return False
        settings = await self._settings(chat_id)
        aliases = settings.get("guardian_aliases") or {}
        return isinstance(aliases, Mapping) and name in {
            str(key).strip().lstrip("/").casefold()[:64] for key in aliases
        }

    async def is_command_disabled(self, chat_id: int, command: str) -> bool:
        settings = await self._settings(chat_id)
        disabled = {str(item).lstrip("/").casefold()[:64] for item in (settings.get("disabled_commands") or [])}
        return str(command or "").lstrip("/").casefold()[:64] in disabled

    @staticmethod
    def _args(args: list[str] | tuple[str, ...] | None) -> list[str]:
        return [_clean(item, 256) for item in (args or ()) if _clean(item, 256)]

    async def execute(
        self,
        command: str,
        *,
        chat_id: int,
        user_id: int,
        args: list[str] | tuple[str, ...] | None = None,
        is_group: bool = True,
        is_admin: bool = False,
        target_user_id: int | None = None,
        target_message_id: int | None = None,
    ) -> GuardianCommandResult | None:
        """Run a non-transport Guardian command.

        ``None`` means the command is a Telegram action and must be handled by
        the typed action executor.  Every mutation is bounded and scoped to the
        current group; no free-form command can select another chat.
        """

        command = str(command or "").casefold().lstrip("/")
        args = self._args(args)
        # ``agbanlar`` historically exposed ``fedbanlist`` while the
        # internal federation branch uses ``fbanlist``. Normalize the public
        # alias before dispatch so it cannot fall through to the action router.
        if command == "fedbanlist":
            command = "fbanlist"
        if command not in self.COMMANDS:
            return None
        if command in {"fedapprove", "fedunapprove"}:
            if not is_group or not is_admin:
                return GuardianCommandResult("Bu işlem grup yöneticilerine açık.")
            return self._unavailable(command)
        if command in {"ghelp", "help"}:
            return GuardianCommandResult(
                "🛡️ <b>AREL GUARDIAN</b>\n\n"
                "Telegram grubunun moderasyon, güvenlik ve federasyon araçları burada.\n"
                "Aşağıdaki başlıklardan birini seçmek için komut adını yazabilirsin:\n\n"
                "<b>Yönetim:</b> /ban /mute /kick /promote /purge /pin\n"
                "<b>Koruma:</b> /antilink /antiraid /captcha /lock /filter /setflood\n"
                "<b>Üye:</b> /warn /userinfo /firstmsg /memberlog /zombies\n"
                "<b>Federasyon:</b> /newfed /joinfed /fban /fedstat /fedbanlist\n"
                "<b>Yapılandırma:</b> /ayarlar /rules /notes /aliases /stats\n\n"
                "Yönetici yetkileri Telegram tarafından ayrıca doğrulanır.",
                metadata={"help": True, "command_count": len(self.COMMANDS)},
            )
        if command == "hizlikur":
            if not is_group:
                return GuardianCommandResult("hızlı kurulum yalnızca gruplarda kullanılabilir")
            if not is_admin:
                return GuardianCommandResult("hızlı kurulumu yalnızca grup yöneticileri başlatabilir")
            settings = await self._settings(chat_id)
            requested = _on(args[0]) if args else None
            enabled = bool(requested) if requested is not None else not bool(settings.get("guardian_setup_complete"))
            defaults = {
                "guardian_setup_complete": enabled,
                "moderation_enabled": enabled,
                "link_filter": enabled,
                "antibotlink_enabled": enabled,
                "antiraid_enabled": enabled,
                "captcha_enabled": enabled,
                "flood_limit": 8 if enabled else 0,
                "flood_mode": "mute",
                "warn_limit": 3,
                "warn_mode": "mute",
                "lock_mode": "delete",
            }
            for key, value in defaults.items():
                await self._set(chat_id, key, value)
            return GuardianCommandResult(
                "Guardian hızlı kurulumu tamamlandı" if enabled else "Guardian hızlı kurulumu sıfırlandı",
                True,
                "guardian_setup_complete",
                enabled,
                document={"settings": defaults},
            )
        if command == "kayitkanali":
            command = "logchannel"
        if command == "yoneticiler":
            return GuardianCommandResult(
                "grup yöneticileri Telegram üzerinden listeleniyor",
                metadata={"transport_action": "admin_list"},
            )
        if command in {"notekle", "notsil"}:
            command = "save" if command == "notekle" else "clear"
        if command == "yetkipaneli":
            command = "yetkiler"
        if command == "language":
            if not is_group:
                return GuardianCommandResult("grup dili yalnızca gruplarda ayarlanabilir")
            if not is_admin:
                return GuardianCommandResult("grup dilini yalnızca yöneticiler değiştirebilir")
            return self._unavailable(command)
        if command in {"start", "help"}:
            return GuardianCommandResult(
                "Guardian komutları Arel'in içinde hazır. /ghelp ile kategorileri, /rules ile grup kurallarını görürsün."
            )
        if command in {"info", "id"}:
            return GuardianCommandResult(
                f"chat id: <code>{int(chat_id)}</code>\nuser id: <code>{int(user_id)}</code>"
            )
        if command == "echo":
            return GuardianCommandResult(" ".join(args)[:4096] or "echo için bir metin yaz")
        if command in {"settitle", "setdesc", "setpic"}:
            if not is_group:
                return GuardianCommandResult("bu Guardian komutu yalnızca gruplarda kullanılabilir")
            if not is_admin:
                return GuardianCommandResult("grup bilgilerini yalnızca yöneticiler değiştirebilir")
            if command == "setpic":
                return GuardianCommandResult(
                    "bu komutu bir fotoğrafa yanıt vererek kullan",
                    metadata={"transport_action": command},
                )
            if not args:
                return GuardianCommandResult(f"kullanım: /{command} metin")
            return GuardianCommandResult(
                f"grup {('başlığı' if command == 'settitle' else 'açıklaması')} güncelleniyor",
                metadata={"transport_action": command, "value": " ".join(args)[:255 if command == 'settitle' else 4096]},
            )
        if command == "afk":
            reason = " ".join(args)[:256] if args else "biraz uzakta"
            await self.data_port.update_user(int(user_id), {"guardian_afk": {"reason": reason, "updated_at": datetime.now(timezone.utc)}})
            return GuardianCommandResult(f"afk kaydı açıldı: {reason}", changed=True)
        # These are Telegram mutations.  They are handled by the typed
        # action executor after this domain surface returns ``None``.
        if command in {"adminmute", "adminunmute"}:
            return None
        if command in {"admin", "deadmin", "muter", "unmuter", "yetkiler"}:
            if not is_group:
                return GuardianCommandResult("bu komut yalnızca gruplarda kullanılabilir")
            if not is_admin:
                return GuardianCommandResult("bu ayrıcalık yalnızca grup yöneticilerine açık")
            target = int(target_user_id or (int(args[0]) if args and args[0].lstrip("-").isdigit() else 0))
            if target <= 0:
                return GuardianCommandResult(f"kullanım: /{command} kullanıcı_id veya bir mesaja yanıt")
            profile = {
                "admin": "full",
                "deadmin": "none",
                "muter": "moderator",
                "unmuter": "remove_restrict",
                "yetkiler": (args[1].casefold() if len(args) > 1 else "panel"),
            }[command]
            return GuardianCommandResult(
                "yönetici izinleri Telegram'a uygulanıyor",
                metadata={"transport_action": "admin_permissions", "target_user_id": target, "profile": profile},
            )
        if command in {"unpinall", "purge"}:
            if not is_group:
                return GuardianCommandResult("bu komut yalnızca gruplarda kullanılabilir")
            if not is_admin:
                return GuardianCommandResult("bu komut yalnızca grup yöneticilerine açık")
            if command == "unpinall":
                return GuardianCommandResult(
                    "sabitli mesajlar kaldırılıyor",
                    metadata={"transport_action": "unpinall"},
                )
            amount = int(args[0]) if args and args[0].isdigit() else (200 if target_message_id else 0)
            if amount < 1 or amount > 200:
                return GuardianCommandResult("kullanım: /purge 1-200 veya bir mesaja yanıt ver")
            return GuardianCommandResult(
                f"son {amount} mesaj temizleniyor",
                metadata={"transport_action": "purge", "amount": amount, "end_message_id": int(target_message_id or 0)},
            )
        if command == "hayaletban":
            if not is_admin:
                return GuardianCommandResult("hayalet banı yalnızca grup yöneticilerine açık")
            target = int(target_user_id or (int(args[0]) if args and args[0].lstrip("-").isdigit() else 0))
            if target <= 0:
                return GuardianCommandResult("kullanım: /hayaletban kullanıcı_id veya bir mesaja yanıt")
            settings = await self._settings(chat_id)
            bans = [int(item) for item in (settings.get("guardian_shadow_bans") or []) if str(item).lstrip("-").isdigit()]
            if target not in bans:
                bans.append(target)
            await self._set(chat_id, "guardian_shadow_bans", bans[-1000:])
            return GuardianCommandResult("kullanıcı hayalet ban listesine alındı", True, "guardian_shadow_bans", bans[-1000:])
        if command == "unsban":
            if not is_admin:
                return GuardianCommandResult("hayalet banı yalnızca grup yöneticileri kaldırabilir")
            target = int(target_user_id or (int(args[0]) if args and args[0].lstrip("-").isdigit() else 0))
            if target <= 0:
                return GuardianCommandResult("kullanım: /golgeac kullanıcı_id veya bir mesaja yanıt")
            settings = await self._settings(chat_id)
            bans = [int(item) for item in (settings.get("guardian_shadow_bans") or []) if str(item).lstrip("-").isdigit()]
            bans = [item for item in bans if item != target]
            await self._set(chat_id, "guardian_shadow_bans", bans)
            return GuardianCommandResult("kullanıcının hayalet banı kaldırıldı", True, "guardian_shadow_bans", bans)
        if command in {"banlimit", "promotelimit", "grupinfolimit"}:
            if not is_admin:
                return GuardianCommandResult("limit ayarlarını yalnızca grup yöneticileri değiştirebilir")
            return self._unavailable(command)
        if command in {"reaksiyon", "fakekoruma", "gorselkoruma", "editkoruma"}:
            if not is_admin:
                return GuardianCommandResult("bu koruma ayarını yalnızca grup yöneticileri değiştirebilir")
            settings = await self._settings(chat_id)
            if command == "reaksiyon":
                values = list(settings.get("guardian_blocked_reactions") or [])
                action = args[0].casefold() if args else "liste"
                if action in {"liste", "list"}:
                    return GuardianCommandResult("engelli reaksiyonlar: " + (", ".join(values) if values else "yok"))
                if action == "temizle":
                    values = []
                elif len(args) >= 2 and action in {"ekle", "sil", "add", "remove"}:
                    emoji = args[1][:16]
                    if action in {"ekle", "add"} and emoji not in values:
                        values.append(emoji)
                    if action in {"sil", "remove"}:
                        values = [item for item in values if item != emoji]
                else:
                    return GuardianCommandResult("kullanım: /reaksiyon ekle|sil emoji, /reaksiyon liste|temizle")
                await self._set(chat_id, "guardian_blocked_reactions", values[:100])
                return GuardianCommandResult("engelli reaksiyonlar güncellendi", True, "guardian_blocked_reactions", values[:100])
            key = {"fakekoruma": "fake_account_protection", "gorselkoruma": "image_protection", "editkoruma": "edit_protection"}[command]
            state = _on(args[0]) if args else None
            if state is None and command == "gorselkoruma" and args and re.fullmatch(r"0(?:\.\d+)?|1(?:\.0)?", args[0]):
                value: Any = float(args[0])
            else:
                value = bool(state) if state is not None else not bool(settings.get(key, False))
            await self._set(chat_id, key, value)
            return GuardianCommandResult(f"{command}: {'açık' if value else 'kapalı'}", True, key, value)
        if command in {"packengelle", "packizinver", "packlistesi", "gifengelle", "gifizinver", "giflistesi"}:
            if command.endswith("listesi"):
                settings = await self._settings(chat_id)
                key = "guardian_blocked_sticker_packs" if command.startswith("pack") else "guardian_blocked_gifs"
                values = list(settings.get(key) or [])
                return GuardianCommandResult(("engelli paketler: " if key.endswith("packs") else "engelli GIF'ler: ") + (", ".join(map(str, values)) if values else "yok"))
            if not is_admin:
                return GuardianCommandResult("bu listeyi yalnızca grup yöneticileri değiştirebilir")
            if not args:
                return GuardianCommandResult(f"kullanım: /{command} kimlik")
            key = "guardian_blocked_sticker_packs" if command.startswith("pack") else "guardian_blocked_gifs"
            values = list((await self._settings(chat_id)).get(key) or [])
            value = _clean(args[0].casefold(), 128)
            if command.endswith("izinver"):
                values = [item for item in values if str(item).casefold() != value]
            elif value not in values:
                values.append(value)
            await self._set(chat_id, key, values[:500])
            return GuardianCommandResult("engelli içerik listesi güncellendi", True, key, values[:500])
        if command in {"rules", "setrules", "privaterules"}:
            settings = await self._settings(chat_id)
            if command == "rules":
                value = _clean(settings.get("guardian_rules") or settings.get("rules"), 4096)
                return GuardianCommandResult(value or "bu grupta henüz kural yazılmamış")
            if not is_admin:
                return GuardianCommandResult("bu Guardian komutu grup yöneticilerine açık")
            if command == "privaterules":
                return self._unavailable(command)
            if not args:
                return GuardianCommandResult("kullanım: /setrules kurallar")
            value = " ".join(args)[:4096]
            await self._set(chat_id, "guardian_rules", value)
            return GuardianCommandResult("grup kuralları güncellendi", True, "guardian_rules", value)
        if command == "blocklistmode" and args and str(args[0]).casefold() in {"delete", "mute", "ban"}:
            if not is_admin:
                return GuardianCommandResult("bu ayar grup y\u00f6neticilerine a\u00e7\u0131k")
            value = str(args[0]).casefold()
            await self._set(chat_id, "blocklist_mode", value)
            await self._set(chat_id, "blocklist_mode_enabled", True)
            return GuardianCommandResult(f"kara liste cezas\u0131: {value}", True, "blocklist_mode", value)
        if command in {"warns", "resetwarn"}:
            if not is_group:
                return GuardianCommandResult("uyarı komutu yalnızca gruplarda kullanılabilir")
            target = int(target_user_id or (int(args[0]) if args and args[0].lstrip("-").isdigit() else user_id))
            settings = await self._settings(chat_id)
            counts = dict(settings.get("guardian_warn_counts") or {})
            key = str(target)
            if command == "resetwarn":
                if not is_admin: return GuardianCommandResult("uyarıları yalnızca yöneticiler sıfırlayabilir")
                counts.pop(key, None)
                await self._set(chat_id, "guardian_warn_counts", counts)
                return GuardianCommandResult("kullanıcının uyarıları sıfırlandı", True, "guardian_warn_counts", counts)
            return GuardianCommandResult(f"kullanıcı uyarıları: {int(counts.get(key, 0) or 0)} / {int(settings.get('warn_limit', 3) or 3)}")
        if command in self._TOGGLE_KEYS:
            if not is_admin:
                return GuardianCommandResult("bu Guardian ayarı grup yöneticilerine açık")
            if command in self._UNWIRED_FEATURES:
                return self._unavailable(command)
            key = self._TOGGLE_KEYS[command]
            state = _on(args[0]) if args else None
            settings = await self._settings(chat_id)
            if state is None:
                state = not bool(settings.get(key, False))
            await self._set(chat_id, key, state)
            labels = {
                "link_filter": "link koruması", "antibotlink_enabled": "bot link koruması",
                "antiraid_enabled": "raid koruması", "captcha_enabled": "doğrulama",
                "clean_service_enabled": "servis temizleyici", "welcome_enabled": "hoş geldin",
                "goodbye_enabled": "veda", "clean_welcome_enabled": "karşılama temizliği",
                "welcome_mute_enabled": "karşılama susturması", "night_mode_enabled": "gece modu",
                "private_rules": "özel kurallar", "blocklist_mode_enabled": "kara liste modu",
            }
            metadata = {"transport_action": "nightmode", "locked": bool(state)} if key == "night_mode_enabled" else {}
            return GuardianCommandResult(f"{labels.get(key, key)} {'on' if state else 'off'}", True, key, state, metadata=metadata)
        if command in {"setwelcome", "setgoodbye"}:
            if not is_admin:
                return GuardianCommandResult("bu ayar grup yöneticilerine açık")
            if not args:
                return GuardianCommandResult(f"kullanım: /{command} mesaj")
            key = "welcome_text" if command == "setwelcome" else "goodbye_text"
            value = " ".join(args)[:1000]
            await self._set(chat_id, key, value)
            return GuardianCommandResult("karşılama metni güncellendi" if command == "setwelcome" else "veda metni güncellendi", True, key, value)
        if command in {"captchatype", "nighttime", "setwarnlimit", "setwarnmode", "setflood", "setfloodmode", "lockmode"}:
            if not is_admin:
                return GuardianCommandResult("bu ayar grup yöneticilerine açık")
            settings = await self._settings(chat_id)
            if command == "captchatype":
                value = (args[0].casefold() if args else "button")
                if value not in {"button", "math", "text"}:
                    return GuardianCommandResult("kullanım: /captchatype button|math|text")
                await self._set(chat_id, "captcha_type", value)
                return GuardianCommandResult(f"doğrulama tipi: {value}", True, "captcha_type", value)
            if command == "nighttime":
                value = args[0] if args else ""
                if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d-(?:[01]\d|2[0-3]):[0-5]\d", value):
                    return GuardianCommandResult("kullanım: /nighttime 22:00-08:00")
                start, end = value.split("-", 1)
                await self._set(chat_id, "night_start", start); await self._set(chat_id, "night_end", end)
                return GuardianCommandResult(f"gece aralığı: {start}–{end}", True, "night_time", value)
            if command == "setflood":
                if not args or not args[0].isdigit():
                    return GuardianCommandResult("kullanım: /setflood 8")
                value = max(3, min(60, int(args[0])))
                await self._set(chat_id, "flood_limit", value)
                return GuardianCommandResult(f"flood limiti: {value}", True, "flood_limit", value)
            if command == "setfloodmode":
                value = (args[0].casefold() if args else "mute")
                if value not in {"delete", "mute", "tmute", "kick", "ban", "tban"}:
                    return GuardianCommandResult("kullanım: /setfloodmode delete|mute|tmute|kick|ban|tban")
                await self._set(chat_id, "flood_mode", value)
                return GuardianCommandResult(f"flood cezası: {value}", True, "flood_mode", value)
            if command == "setwarnlimit":
                if not args or not args[0].isdigit():
                    return GuardianCommandResult("kullanım: /setwarnlimit 3")
                value = max(1, min(20, int(args[0])))
                await self._set(chat_id, "warn_limit", value)
                return GuardianCommandResult(f"uyarı sınırı: {value}", True, "warn_limit", value)
            if command == "setwarnmode":
                value = (args[0].casefold() if args else "mute")
                if value not in {"mute", "ban", "kick", "tban"}:
                    return GuardianCommandResult("kullanım: /setwarnmode mute|ban|kick|tban")
                await self._set(chat_id, "warn_mode", value)
                return GuardianCommandResult(f"uyarı cezası: {value}", True, "warn_mode", value)
            value = (args[0].casefold() if args else "delete")
            if value not in {"delete", "mute", "ban"}:
                return GuardianCommandResult("kullanım: /lockmode delete|mute|ban")
            await self._set(chat_id, "lock_mode", value)
            return GuardianCommandResult(f"kilit modu: {value}", True, "lock_mode", value)
        if command in self._LIST_KEYS:
            if command in {"approved", "locks", "blocklist", "whitelist", "mban", "munban"} and not args:
                settings = await self._settings(chat_id)
                key = self._LIST_KEYS[command]
                values = settings.get(key) or []
                if isinstance(values, Mapping):
                    values = list(values)
                values = [_clean(value, 128) for value in values][:100]
                label = {"approved_users": "onaylı kullanıcılar", "locked_types": "kilitler", "blocked_words": "kara liste", "allowed_domains": "izinli alan adları", "media_bans": "medya yasakları"}.get(key, key)
                return GuardianCommandResult(f"{label}: {', '.join(values) if values else 'yok'}")
            if not is_admin:
                return GuardianCommandResult("bu Guardian listesi grup yöneticilerine açık")
            settings = await self._settings(chat_id)
            key = self._LIST_KEYS[command]
            values = settings.get(key) or []
            if not isinstance(values, list): values = list(values) if isinstance(values, (tuple, set)) else []
            token = _clean(args[0].casefold() if args else "", 128)
            if not token:
                return GuardianCommandResult(f"kullanım: /{command} değer")
            remove = command in {"rmblock", "rmwhitelist", "munban", "disapprove", "unlock"} or token in {"sil", "remove", "rm"}
            if remove:
                target = _clean(args[1].casefold() if len(args) > 1 else token, 128)
                values = [item for item in values if _clean(item).casefold() != target]
            else:
                raw = args if command in {"addblock", "whitelist", "approve", "lock", "mban"} else [token]
                for item in raw:
                    item = _clean(item.casefold(), 128)
                    if item and item not in values: values.append(item)
            await self._set(chat_id, key, values[:500])
            return GuardianCommandResult(f"{key}: {', '.join(values) if values else 'yok'}", True, key, values[:500])
        if command in {"alias", "delalias", "aliases"}:
            if command == "aliases":
                settings = await self._settings(chat_id)
                aliases = settings.get("guardian_aliases") or {}
                rows = [f"/{_clean(k,32)} → /{_clean(v,64)}" for k, v in aliases.items()][:50] if isinstance(aliases, Mapping) else []
                return GuardianCommandResult("takma adlar:\n" + ("\n".join(rows) if rows else "yok"))
            if not is_admin:
                return GuardianCommandResult("takma adları yalnızca yöneticiler değiştirebilir")
            if len(args) < 2:
                return GuardianCommandResult(f"kullanım: /{command} takma_ad komut")
            settings = await self._settings(chat_id); aliases = dict(settings.get("guardian_aliases") or {})
            name = re.sub(r"[^a-z0-9_]", "", args[0].casefold())[:32]
            if not name: return GuardianCommandResult("geçerli bir takma ad yaz")
            if command == "delalias": aliases.pop(name, None)
            else: aliases[name] = args[1].lstrip("/").casefold()[:64]
            await self._set(chat_id, "guardian_aliases", aliases)
            return GuardianCommandResult("takma ad güncellendi", True, "guardian_aliases", aliases)
        if command in {"notes", "save", "get", "clear"}:
            settings = await self._settings(chat_id); notes = dict(settings.get("guardian_notes") or {})
            if command == "notes":
                return GuardianCommandResult("notlar:\n" + ("\n".join(f"• {k}: {v}" for k, v in list(notes.items())[:50]) or "yok"))
            if not args:
                return GuardianCommandResult(f"kullanım: /{command} ad [metin]")
            key = _clean(args[0].casefold(), 64)
            if command == "get": return GuardianCommandResult(_clean(notes.get(key) or "not bulunamadı", 4096))
            if not is_admin:
                return GuardianCommandResult("notları yalnızca grup yöneticileri değiştirebilir")
            if command == "clear": notes.pop(key, None)
            else: notes[key] = " ".join(args[1:])[:2000]
            await self._set(chat_id, "guardian_notes", notes)
            return GuardianCommandResult("not güncellendi", True, "guardian_notes", notes)
        if command in {"disable", "enable", "disabled"}:
            settings = await self._settings(chat_id); disabled = list(settings.get("disabled_commands") or [])
            if command == "disabled": return GuardianCommandResult("kapalı komutlar: " + (", ".join(disabled) if disabled else "yok"))
            if not is_admin: return GuardianCommandResult("komut kapatma ayarı grup yöneticilerine açık")
            if not args: return GuardianCommandResult(f"kullanım: /{command} komut")
            name = args[0].lstrip("/").casefold()[:64]
            if command == "disable" and name not in disabled: disabled.append(name)
            if command == "enable": disabled = [item for item in disabled if item != name]
            await self._set(chat_id, "disabled_commands", disabled[:200])
            return GuardianCommandResult("komut durumu güncellendi", True, "disabled_commands", disabled)
        if command in {"mod", "unmod", "free", "unfree", "staff"}:
            if command == "staff":
                if not is_group:
                    return GuardianCommandResult("ekip listesi yalnızca gruplarda kullanılabilir")
                if not is_admin:
                    return GuardianCommandResult("ekip listesini yalnızca grup yöneticileri görebilir")
                return GuardianCommandResult(
                    "Guardian ekibi Telegram'dan listeleniyor",
                    metadata={"transport_action": "staff_list"},
                )
            if not is_admin: return GuardianCommandResult("rolleri yalnızca grup yöneticileri değiştirebilir")
            if not args or not args[0].lstrip("-").isdigit(): return GuardianCommandResult(f"kullanım: /{command} kullanıcı_id")
            settings = await self._settings(chat_id); roles = dict(settings.get("guardian_roles") or {})
            target = str(int(args[0])); role = command if command not in {"unmod", "unfree"} else "member"
            if role == "member": roles.pop(target, None)
            else: roles[target] = role
            await self._set(chat_id, "guardian_roles", roles)
            return GuardianCommandResult("kullanıcı rolü güncellendi", True, "guardian_roles", roles)
        if command in {"recurring", "stoprecurring"}:
            if not is_admin: return GuardianCommandResult("tekrarlı mesajları yalnızca yöneticiler ayarlayabilir")
            settings = await self._settings(chat_id); rows = list(settings.get("guardian_recurring") or [])
            if command == "stoprecurring":
                if args and args[0].isdigit(): rows = [row for row in rows if str(row.get("id")) != args[0]]
                else: rows = []
            else:
                if len(args) < 2: return GuardianCommandResult("kullanım: /recurring dakika mesaj")
                if not args[0].isdigit(): return GuardianCommandResult("süre dakika olarak sayı olmalı")
                rows.append({"id": len(rows) + 1, "interval_minutes": max(1, min(10080, int(args[0]))), "text": " ".join(args[1:])[:1000], "enabled": True})
            await self._set(chat_id, "guardian_recurring", rows[:50])
            return GuardianCommandResult("tekrarlı mesaj ayarı güncellendi", True, "guardian_recurring", rows)
        if command in {"gban", "ungban"}:
            if not is_admin: return GuardianCommandResult("genel yasak ayarı yalnızca yöneticilere açık")
            raw_target = target_user_id if target_user_id is not None else (int(args[0]) if args and args[0].lstrip("-").isdigit() else 0)
            if int(raw_target or 0) <= 0: return GuardianCommandResult(f"kullanım: /{command} kullanıcı_id, @kullanıcı veya bir mesaja yanıt")
            user = await self.data_port.get_user(int(raw_target)) or {}
            existing = dict(user.get("guardian_gban") or {}) if isinstance(user, Mapping) and isinstance(user.get("guardian_gban"), Mapping) else {}
            if command == "gban":
                existing = {"enabled": True, "reason": " ".join(args[1:])[:256] or "Guardian genel yasağı"}
            else:
                existing = {}
            await self.data_port.update_user(int(raw_target), {"guardian_gban": existing})
            return GuardianCommandResult("genel yasak kaydı güncellendi", True, "guardian_gban", existing)
        if command in {"fedchats", "fedapproved", "setappeallog", "fednotes", "fedtransfer", "feddelete", "setfedlog", "fnote", "unsubfed", "fedsubs", "fbanlist", "importfbans", "whois", "toprisk", "shadowstats", "analytics"}:
            settings = await self._settings(chat_id)
            federation = dict(settings.get("guardian_federation") or {})
            if not is_admin:
                return GuardianCommandResult("bu federasyon ve risk komutu grup yöneticilerine açık")
            if command == "fedchats":
                chats = list(federation.get("chats") or [chat_id])
                return GuardianCommandResult("federasyona bağlı sohbetler: " + ", ".join(map(str, chats)))
            if command == "fedapproved":
                values = list(federation.get("approved_users") or [])
                return GuardianCommandResult("federasyon güvenilir listesi: " + (", ".join(map(str, values)) if values else "yok"))
            if command in {"setappeallog", "setfedlog"}:
                return self._unavailable(command)
            if command == "fednotes":
                target = str(int(args[0])) if args and args[0].lstrip("-").isdigit() else str(target_user_id or user_id)
                notes = dict(federation.get("notes") or {})
                values = list(notes.get(target) or [])
                return GuardianCommandResult(f"federasyon notları ({target}): " + ("\n".join(map(str, values[-20:])) if values else "yok"))
            if command == "fnote":
                if len(args) < 2 or not args[0].lstrip("-").isdigit():
                    return GuardianCommandResult("kullanım: /fnote kullanıcı_id not")
                target = str(int(args[0])); notes = dict(federation.get("notes") or {})
                values = list(notes.get(target) or []); values.append(" ".join(args[1:])[:500]); notes[target] = values[-20:]
                federation["notes"] = notes; await self._set(chat_id, "guardian_federation", federation)
                return GuardianCommandResult("federasyon notu eklendi", True, "guardian_federation", federation)
            if command == "fedtransfer":
                if not args or not args[0].lstrip("-").isdigit():
                    return GuardianCommandResult("kullanım: /fedtransfer kullanıcı_id")
                federation["owner_id"] = int(args[0]); await self._set(chat_id, "guardian_federation", federation)
                return GuardianCommandResult("federasyon sahipliği aktarıldı", True, "guardian_federation", federation)
            if command == "feddelete":
                await self._set(chat_id, "guardian_federation", {}); await self._set(chat_id, "guardian_fed_bans", [])
                return GuardianCommandResult("federasyon ve bağlı yasak kayıtları silindi", True, "guardian_federation", {})
            if command in {"unsubfed", "fedsubs"}:
                subs = list(settings.get("guardian_federation_subs") or [])
                if command == "fedsubs":
                    return GuardianCommandResult("federasyon abonelikleri: " + (", ".join(map(str, subs)) if subs else "yok"))
                if not args: return GuardianCommandResult("kullanım: /unsubfed federasyon_id")
                subs = [item for item in subs if str(item) != args[0]]
                await self._set(chat_id, "guardian_federation_subs", subs)
                return GuardianCommandResult("federasyon aboneliği kaldırıldı", True, "guardian_federation_subs", subs)
            if command == "fbanlist":
                bans = list(settings.get("guardian_fed_bans") or [])
                return GuardianCommandResult("federasyon yasakları: " + (", ".join(map(str, bans)) if bans else "yok"), document={"bans": bans})
            if command == "importfbans":
                try: incoming = json.loads(" ".join(args))
                except (TypeError, ValueError, json.JSONDecodeError): return GuardianCommandResult("geçersiz federasyon yasak listesi")
                if not isinstance(incoming, list): return GuardianCommandResult("yasak listesi dizi olmalı")
                bans = [int(item) for item in incoming if str(item).lstrip("-").isdigit()][:500]
                await self._set(chat_id, "guardian_fed_bans", bans)
                return GuardianCommandResult(f"{len(bans)} federasyon yasağı içe aktarıldı", True, "guardian_fed_bans", bans)
            if command in {"whois", "shadowstats", "toprisk", "analytics"}:
                target = int(args[0]) if args and args[0].lstrip("-").isdigit() else int(target_user_id or user_id)
                user = await self.data_port.get_user(target) or {}
                risk = user.get("guardian_risk") if isinstance(user, Mapping) else {}
                if command == "whois":
                    return GuardianCommandResult(f"risk kartı\nkullanıcı: <code>{target}</code>\nrisk: {int((risk or {}).get('score', 0) or 0)}")
                if command == "shadowstats":
                    stats = (risk or {}).get("shadow") if isinstance(risk, Mapping) else {}
                    return GuardianCommandResult(f"gölge ban istatistiği\nengellenen mesaj: {int((stats or {}).get('messages', 0) or 0)}")
                if command == "analytics":
                    return GuardianCommandResult("federasyon analizleri kayıtlı olaylar üzerinden hazırlanıyor", document={"chat_id": int(chat_id)})
                return GuardianCommandResult("en riskli kullanıcı raporu için kalıcı risk kayıtları gerekli", document={"chat_id": int(chat_id)})
        if command in {"connect", "disconnect", "fsub", "logchannel", "broadcast", "broadcastpin", "export", "import", "zombies", "reload", "stats", "userinfo", "firstmsg", "memberlog", "addsudo", "delsudo"}:
            if not is_admin and command not in {"stats", "userinfo", "firstmsg", "memberlog"}:
                return GuardianCommandResult("bu Guardian komutu yönetici yetkisi ister")
            if command == "export":
                settings = await self._settings(chat_id)
                safe = {k: v for k, v in settings.items() if str(k).startswith("guardian_") or k in {"blocked_words", "allowed_domains", "flood_limit", "flood_mode", "locked_types"}}
                return GuardianCommandResult("Guardian ayar yedeği hazır", document={"chat_id": int(chat_id), "settings": safe})
            if command == "import":
                if not args:
                    return GuardianCommandResult("kullanım: /import <json>")
                try:
                    payload = json.loads(" ".join(args))
                except (TypeError, ValueError, json.JSONDecodeError):
                    return GuardianCommandResult("geçersiz Guardian yedeği")
                incoming = payload.get("settings") if isinstance(payload, Mapping) else None
                if not isinstance(incoming, Mapping):
                    return GuardianCommandResult("yedek biçimi tanınmadı")
                allowed = {
                    "guardian_", "blocked_words", "allowed_domains", "flood_limit", "flood_mode",
                    "locked_types", "media_bans", "welcome_enabled", "goodbye_enabled", "captcha_enabled",
                }
                copied = 0
                for key, value in incoming.items():
                    key = _clean(key, 80)
                    if not any(key == item or key.startswith(item) for item in allowed):
                        continue
                    if isinstance(value, (str, int, float, bool, list, dict)):
                        await self._set(chat_id, key, value)
                        copied += 1
                return GuardianCommandResult(f"Guardian yedeği içe aktarıldı: {copied} ayar", True)
            if command in {"connect", "disconnect"}:
                return self._unavailable(command)
            if command == "fsub":
                settings = await self._settings(chat_id)
                if not args:
                    value = _clean(settings.get("fsub_channel"), 128)
                    display_value = value or "kapalı"
                    return GuardianCommandResult(f"zorunlu kanal: {display_value}\nKullanım: /fsub @kanal veya /fsub off")
                value = _clean(args[0], 128)
                if value.casefold() in {"off", "kapat", "kapal\u0131", "kapali"}:
                    await self._set(chat_id, "fsub_channel", None)
                    return GuardianCommandResult("zorunlu kanal korumas\u0131 kapat\u0131ld\u0131", True, "fsub_channel", None)
                if not re.fullmatch(r"(?:@?[A-Za-z0-9_]{3,64}|-100\d{5,})", value):
                    return GuardianCommandResult("kanal kullan\u0131c\u0131 ad\u0131 veya sohbet kimli\u011fi ge\u00e7ersiz")
                if not value.startswith("@") and not value.startswith("-100"):
                    value = "@" + value
                await self._set(chat_id, "fsub_channel", value)
                return GuardianCommandResult(f"zorunlu kanal: {value}", True, "fsub_channel", value)
            if command == "logchannel":
                return self._unavailable(command)
                settings = await self._settings(chat_id)
                if not args:
                    value = settings.get("guardian_log_channel")
                    display_value = value if value else "kapalı"
                    return GuardianCommandResult(f"kayıt kanalı: {display_value}")
                if args[0].casefold() in {"off", "kapat", "kapal\u0131", "kapali"}:
                    await self._set(chat_id, "guardian_log_channel", None)
                    return GuardianCommandResult("kay\u0131t kanal\u0131 kapat\u0131ld\u0131", True, "guardian_log_channel", None)
                if not args[0].lstrip("-").isdigit():
                    return GuardianCommandResult("kullan\u0131m: /logchannel kanal_id")
                value = int(args[0])
                await self._set(chat_id, "guardian_log_channel", value)
                return GuardianCommandResult(f"kay\u0131t kanal\u0131: {value}", True, "guardian_log_channel", value)
            if command == "reload":
                settings = await self._settings(chat_id)
                active = []
                labels = {
                    "link_filter": "anti-link", "antibotlink_enabled": "anti-bot-link",
                    "antiraid_enabled": "anti-raid", "captcha_enabled": "captcha",
                    "night_mode_enabled": "gece modu", "fsub_channel": "zorunlu kanal",
                    "welcome_enabled": "kar\u015f\u0131lama", "goodbye_enabled": "veda",
                    "clean_service_enabled": "servis temizleme",
                }
                for key, label in labels.items():
                    if settings.get(key):
                        active.append(label)
                flood = settings.get("flood_limit")
                if flood:
                    active.append(f"flood ({int(flood)})")
                return GuardianCommandResult(
                    "Guardian ayarlar\u0131 yeniden y\u00fcklendi\n" +
                    ("aktif korumalar: " + ", ".join(active) if active else "aktif koruma yok"),
                    document={"chat_id": int(chat_id), "active": active, "settings_count": len(settings)},
                )
            if command == "stats":
                settings = await self._settings(chat_id)
                enabled = [str(k) for k, v in settings.items() if v not in (None, False, [], {}, "")]
                return GuardianCommandResult(
                    f"Guardian durumu\naktif ayar: {len(enabled)}\n" +
                    ("korumalar: " + ", ".join(enabled[:20]) if enabled else "korumalar: yok"),
                    document={"chat_id": int(chat_id), "active_settings": enabled[:50]},
                )
            if command in {"userinfo", "firstmsg", "memberlog"}:
                target = int(args[0]) if args and args[0].lstrip("-").isdigit() else int(target_user_id or user_id)
                if command in {"firstmsg", "memberlog"} and not is_admin:
                    return GuardianCommandResult("üye mesaj geçmişini yalnızca grup yöneticileri görebilir")
                if command == "userinfo" and not is_admin and target != int(user_id):
                    return GuardianCommandResult("başka üyelerin profilini yalnızca grup yöneticileri görebilir")
                user = await self.data_port.get_user(target) or {}
                name = html.escape(_clean(user.get("first_name") or user.get("username") or "kullan\u0131c\u0131", 80))
                if command == "userinfo":
                    return GuardianCommandResult(
                        f"kullan\u0131c\u0131: <code>{target}</code>\nad: {name}\nson g\u00f6r\u00fclme: {_clean(user.get('last_seen') or 'bilinmiyor', 80)}",
                        document={"user_id": target},
                    )
                if command == "firstmsg":
                    getter = getattr(self.data_port, "get_first_message", None)
                    row = await getter(chat_id, target) if callable(getter) else None
                    if not row:
                        return GuardianCommandResult("bu kullan\u0131c\u0131 i\u00e7in saklanan ilk mesaj bulunamad\u0131")
                    content = html.escape(_clean(row.get("content") or "[medya]", 600))
                    stamp = html.escape(_clean(row.get("timestamp") or "", 64))
                    return GuardianCommandResult(f"ilk mesaj\n<code>{stamp}</code>\n{content}")
                getter = getattr(self.data_port, "get_member_message_log", None)
                rows = await getter(chat_id, target, limit=20) if callable(getter) else []
                if not rows:
                    return GuardianCommandResult("bu kullan\u0131c\u0131 i\u00e7in mesaj ge\u00e7mi\u015fi yok")
                lines = [f"son mesajlar ({target})"]
                for row in rows[-20:]:
                    stamp = html.escape(_clean(row.get("timestamp") or "", 32))
                    content = html.escape(_clean(row.get("content") or "[medya]", 180))
                    lines.append(f"\u2022 {stamp}: {content}")
                return GuardianCommandResult("\n".join(lines))
            if command == "zombies":
                days = int(args[0]) if args and args[0].isdigit() else 30
                days = max(7, min(3650, days))
                getter = getattr(self.data_port, "get_inactive_users", None)
                rows = await getter(chat_id, datetime.now(timezone.utc) - timedelta(days=days), limit=50) if callable(getter) else []
                ids = [str(row.get("_id")) for row in rows if row.get("_id") is not None]
                return GuardianCommandResult(
                    f"son {days} g\u00fcnde mesaj\u0131 g\u00f6r\u00fclmeyen kay\u0131tl\u0131 \u00fcyeler: " + (", ".join(ids) if ids else "yok"),
                    document={"days": days, "user_ids": ids},
                )
            if command == "broadcastpin":
                return GuardianCommandResult(
                    "yay\u0131n ve sabitleme i\u00e7in metin gerekli" if not args else "yay\u0131n ve sabitleme i\u00e7in ta\u015f\u0131ma haz\u0131r",
                    metadata={"transport_action": "broadcastpin", "text": " ".join(args)[:3500]},
                )
            return GuardianCommandResult(f"/{command} Arel içindeki Guardian servisine bağlandı; bu komut için işlem kuyruğu hazır.", metadata={"command": command})
        return None

    async def record_warning(self, chat_id: int, target_user_id: int, *, reason: str = "guardian") -> tuple[int, int, str]:
        """Persist one idempotent-friendly warning counter for a group."""
        settings = await self._settings(chat_id)
        counts = dict(settings.get("guardian_warn_counts") or {})
        key = str(int(target_user_id))
        counts[key] = min(100, int(counts.get(key, 0) or 0) + 1)
        limit = max(1, min(20, int(settings.get("warn_limit", 3) or 3)))
        mode = _clean(settings.get("warn_mode") or "mute", 32).casefold()
        await self._set(chat_id, "guardian_warn_counts", counts)
        return counts[key], limit, mode


__all__ = ["GuardianCommandResult", "GuardianSurface"]
