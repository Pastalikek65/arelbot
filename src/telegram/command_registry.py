"""Typed Telegram command catalogue used by Arel's command menus.

The Telegram API has one command list per scope, while compatibility aliases
still need to remain callable by users who have old help messages bookmarked.
This module keeps those concerns separate:

* canonical names are the only names returned by :meth:`CommandRegistry.menu_rows`;
* aliases resolve to the canonical definition but never appear in a menu;
* visibility is evaluated by Telegram scope (public, group administrator, or
  owner), and is not used as an authorization substitute by handlers.

The registry deliberately does not import ``telegram`` or any handler module.
It is therefore safe to use from architecture checks, command registration,
and replay tests without constructing the application.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Sequence


class CommandScope(str, Enum):
    """The narrowest Telegram menu scope in which a command is visible."""

    PUBLIC = "public"
    GROUP_ADMIN = "group_admin"
    OWNER = "owner"


def _normalize_name(value: str) -> str:
    name = str(value or "").strip().lstrip("/").casefold()
    if not name:
        raise ValueError("command_name_empty")
    # Telegram command names are ASCII protocol identifiers.  Keep this
    # validation here so a Turkish display phrase cannot accidentally become
    # a command identifier; display text belongs in ``description``.
    if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in name):
        raise ValueError(f"command_name_invalid:{name}")
    return name


@dataclass(frozen=True)
class CommandDefinition:
    """One canonical command and its hidden backwards-compatible aliases."""

    name: str
    description: str
    handler: str
    scope: CommandScope = CommandScope.PUBLIC
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        name = _normalize_name(self.name)
        aliases = tuple(_normalize_name(alias) for alias in self.aliases)
        if name in aliases:
            raise ValueError(f"command_alias_is_canonical:{name}")
        if len(set(aliases)) != len(aliases):
            raise ValueError(f"command_alias_duplicate:{name}")
        if not str(self.description or "").strip():
            raise ValueError(f"command_description_empty:{name}")
        if not str(self.handler or "").strip():
            raise ValueError(f"command_handler_empty:{name}")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "scope", CommandScope(self.scope))
        object.__setattr__(self, "aliases", aliases)


@dataclass(frozen=True)
class ResolvedCommand:
    """Resolution result retaining whether the caller used a hidden alias."""

    definition: CommandDefinition
    matched_name: str

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def description(self) -> str:
        return self.definition.description

    @property
    def handler(self) -> str:
        return self.definition.handler

    @property
    def scope(self) -> CommandScope:
        return self.definition.scope

    @property
    def is_legacy_alias(self) -> bool:
        return self.matched_name != self.definition.name


_SCOPE_RANK = {
    CommandScope.PUBLIC: 0,
    CommandScope.GROUP_ADMIN: 1,
    CommandScope.OWNER: 2,
}


# Existing rows in ``main.py`` are kept during the rolling migration.  This
# map is intentionally explicit: an alias never guesses its target from a
# description, which would make a future command rename unsafe.
LEGACY_ALIAS_MAP: Mapping[str, str] = {
    "dl": "indir",
    "download": "indir",
    "liderlik": "leaderboard",
    "lb": "leaderboard",
    "profil": "profile",
    "mod": "moderasyon",
    "documents": "belgeler",
    "hatirlat": "remind",
    "bildirimayar": "bildirim_ayar",
    "bakiye": "ekonomi",
    "bildirimler": "bildirim",
    "takipler": "takip",
    "filmoneri": "kultur",
    "dizioneri": "kultur",
    "kitaponeri": "kultur",
    "sarkioneri": "kultur",
    "soz": "lyrics",
    "sarki": "lyrics",
    "search": "ara",
    "gunlukplan": "plan",
    "haftalikplan": "plan",
    "daily_brief": "gunluk_ozet",
}


# Explicit semantic bindings for the rolling command migration.  The registry
# never guesses a handler from a command's name or description: a command is
# listed here only when the existing Orchestrator method has the same input
# and output contract. Guardian moderation commands are registered below and
# routed through the embedded typed compatibility layer; they do not create a
# second Telegram bot or a second persistence API.
#
# Keeping this map next to the typed catalogue makes the mapping inspectable by
# architecture/replay checks and lets canonical menus, hidden aliases and the
# legacy command rows all resolve to one callback during the migration.
EXPLICIT_HANDLER_MAP: Mapping[str, str] = {
    "ekonomi": "handle_economy_command",
    "magaza": "handle_economy_command",
    "envanter": "handle_economy_command",
    "harfmaraton": "handle_puzzle_command",
    "sozcukavi": "handle_puzzle_command",
    "yasakharf": "handle_puzzle_command",
    "ortakhikaye": "handle_puzzle_command",
    "bomba": "handle_puzzle_command",
    "korsiralama": "handle_puzzle_command",
    "kronoloji": "handle_puzzle_command",
    "dogruyanlis": "handle_puzzle_command",
    "dedektif": "handle_puzzle_command",
    "amiral": "handle_puzzle_command",
    "ikidogru": "handle_puzzle_command",
    "kimimben": "handle_puzzle_command",
    "cizbil": "handle_puzzle_command",
    "takimligi": "handle_puzzle_command",
    "kadeh": "handle_puzzle_command",
    "pisti": "handle_puzzle_command",
    "papazkimde": "handle_puzzle_command",
    "2048": "handle_puzzle_command",
    "besipucu": "handle_puzzle_command",
    "emojibilmece": "handle_puzzle_command",
    "wordle": "handle_puzzle_command",
    "karsikelime": "handle_puzzle_command",
    "anagram": "handle_puzzle_command",
    "eslestirme": "handle_puzzle_command",
    "mayin": "handle_puzzle_command",
    "sudoku": "handle_puzzle_command",
    "sayitahmin": "handle_puzzle_command",
    "refleks": "handle_puzzle_command",
    "sifre": "handle_puzzle_command",
    "fark": "handle_puzzle_command",
    # Public information providers.
    "haber": "handle_haber_command",
    "deprem": "handle_deprem_command",
    "hava": "handle_hava_command",
    "hava_kalite": "handle_air_quality_command",
    "akaryakit": "handle_akaryakit_command",
    "doviz": "handle_doviz_command",
    "altin": "handle_doviz_command",
    "eczane": "handle_eczane_command",
    "ucak": "handle_ucak_command",
    "ezan": "handle_ezan_command",
    "bim": "handle_bim_command",
    "sehir": "handle_city_command",
    "plaka": "handle_city_command",
    "telefon": "handle_city_command",
    "ilce": "handle_city_command",
    "sehirler": "handle_city_command",
    "kultur": "handle_culture_command",
    "filmoneri": "handle_culture_command",
    "dizioneri": "handle_culture_command",
    "kitaponeri": "handle_culture_command",
    "sarkioneri": "handle_culture_command",
    "hikaye": "handle_story_command",
    "soz": "handle_lyrics_command",
    "sarki": "handle_lyrics_command",
    "lyrics": "handle_lyrics_command",
    # Personal productivity and notifications.
    "remind": "handle_remind_command",
    "hatirlat": "handle_remind_command",
    "hatirlatmalar": "handle_reminders_command",
    "bildirim_ayar": "handle_notification_settings_command",
    "bildirimayar": "handle_notification_settings_command",
    "takip": "handle_subscription_command",
    "bildirim": "handle_subscription_command",
    "takipler": "handle_subscription_command",
    "bildirimler": "handle_subscription_command",
    "not": "handle_note_command",
    "notlar": "handle_notes_command",
    "gorev": "handle_task_command",
    "gorevler": "handle_tasks_command",
    "plan": "handle_plan_command",
    "gunlukplan": "handle_plan_command",
    "haftalikplan": "handle_plan_command",
    "gunluk_ozet": "handle_daily_brief_command",
    "daily_brief": "handle_daily_brief_command",
    "alarm": "handle_alert_command",
    "duyuru": "handle_announcement_command",
    # Quote and sticker surfaces.
    "q": "handle_q_command",
    "qtop": "handle_qtop_command",
    "qrand": "handle_qrand_command",
    "qd": "handle_qd_command",
    "qtemplate": "handle_qtemplate_command",
    "qcolor": "handle_qcolor_command",
    "qemoji": "handle_qemoji_command",
    "qrate": "handle_qrate_command",
    "qtheme": "handle_qtheme_command",
    "sticker": "handle_sticker_command",
    "st": "handle_sticker_search",
    "catalog": "handle_catalog",
    "packs": "handle_packs_command",
    "stdelete": "handle_stdelete_command",
    "stpack": "handle_stpack_command",
    # Games and interaction.
    "quiz": "handle_quiz_command",
    "trivia": "handle_quiz_command",
    "dogruluk": "handle_truth_dare_command",
    "cesaret": "handle_truth_dare_command",
    "d": "handle_truth_dare_command",
    "c": "handle_truth_dare_command",
    "sisecevir": "handle_sisecevir_command",
    "rusruleti": "handle_rusruleti_command",
    "duello": "handle_duello_command",
    "zar": "handle_zar_command",
    "yazitura": "handle_yazitura_command",
    "slot": "handle_slot_command",
    "adamasmaca": "handle_hangman_command",
    "kelimeoyunu": "handle_wordgame_command",
    "connect4": "handle_connect4_command",
    "xox": "handle_tictactoe_command",
    "tictactoe": "handle_tictactoe_command",
    "gomoku": "handle_gomoku_command",
    "besli": "handle_gomoku_command",
    "batak": "handle_batak_command",
    "blackjack": "handle_blackjack_command",
    "anlat": "handle_anlat_command",
    "oyun": "handle_oyun_command",
    "oyunbitir": "handle_oyunbitir_command",
    "iptal": "handle_oyunbitir_command",
    "bitir": "handle_oyunbitir_command",
    # Existing Arel group surfaces with exact method contracts.
    "grupayari": "handle_ayar_command",
    "grupistatistik": "handle_group_analytics_command",
    "sohbetmodu": "handle_sohbet_command",
    "areluslubu": "handle_personality_command",
    # Kek Guardian is embedded in Arel.  Turkish names remain hidden
    # compatibility aliases, while the callback is the shared Guardian
    # dispatcher instead of the old Arel moderation handlers.
    "sustur": "handle_guardian_command",
    "susturac": "handle_guardian_command",
    "yasakla": "handle_guardian_command",
    "yasagiac": "handle_guardian_command",
    "uyar": "handle_guardian_command",
    "uyarlar": "handle_guardian_command",
    "uyarisifirla": "handle_guardian_command",
    "tekme": "handle_guardian_command",
    "geciciyasa": "handle_guardian_command",
    "mesajisil": "handle_guardian_command",
    "mesajtemizle": "handle_guardian_command",
    "sabitle": "handle_guardian_command",
    "sabitlekaldir": "handle_guardian_command",
    "rapor": "handle_guardian_command",
    "yasaklikelimeler": "handle_guardian_command",
    "iznilinkler": "handle_guardian_command",
    "floodlimiti": "handle_guardian_command",
    "floodcezasi": "handle_guardian_command",
    "grupkurallari": "handle_guardian_command",
}


class CommandRegistry:
    """Immutable-at-runtime command index with collision checks."""

    def __init__(self, definitions: Iterable[CommandDefinition] = ()) -> None:
        self._definitions: tuple[CommandDefinition, ...] = tuple(definitions)
        self._index: dict[str, CommandDefinition] = {}
        self._aliases: set[str] = set()
        for definition in self._definitions:
            self._register(definition)

    def _register(self, definition: CommandDefinition) -> None:
        names = (definition.name, *definition.aliases)
        for name in names:
            if name in self._index:
                previous = self._index[name]
                raise ValueError(
                    f"command_collision:{name}:{previous.name}:{definition.name}"
                )
            self._index[name] = definition
        self._aliases.update(definition.aliases)

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[tuple[str, str]],
        *,
        hidden_aliases: Iterable[str] = (),
        hidden_alias_map: Mapping[str, str] | None = None,
        group_admin_commands: Iterable[str] = (),
        owner_commands: Iterable[str] = (),
        handler_by_command: Mapping[str, str] | None = None,
    ) -> "CommandRegistry":
        """Build a registry from the existing ``main.py`` rows.

        ``rows`` remains the source of truth for bootstrap registration during
        migration.  This adapter lets the new scope-aware menu code be added
        without requiring a risky all-at-once handler rewrite.
        """

        hidden = {_normalize_name(item) for item in hidden_aliases}
        group_admin = {_normalize_name(item) for item in group_admin_commands}
        owner = {_normalize_name(item) for item in owner_commands}
        handlers = {
            _normalize_name(key): str(value)
            for key, value in (handler_by_command or {}).items()
        }
        definitions: list[CommandDefinition] = []
        descriptions: dict[str, str] = {}
        seen: set[str] = set()
        for raw_name, description in rows:
            name = _normalize_name(raw_name)
            descriptions[name] = str(description)
            if name in seen or name in hidden:
                continue
            seen.add(name)
            scope = (
                CommandScope.OWNER
                if name in owner
                else CommandScope.GROUP_ADMIN
                if name in group_admin
                else CommandScope.PUBLIC
            )
            definitions.append(
                CommandDefinition(
                    name=name,
                    description=str(description),
                    handler=handlers.get(name, name),
                    scope=scope,
                )
            )
        by_name = {definition.name: definition for definition in definitions}
        alias_map = {
            _normalize_name(alias): _normalize_name(target)
            for alias, target in (hidden_alias_map or {}).items()
        }
        # Rebuild definitions with aliases only when the canonical target is
        # active in this bootstrap surface.  This keeps aliases resolvable
        # while preventing an alias from accidentally becoming a menu item.
        rebuilt: list[CommandDefinition] = []
        for definition in definitions:
            aliases = tuple(
                alias
                for alias, target in alias_map.items()
                if target == definition.name and alias in descriptions
            )
            rebuilt.append(
                CommandDefinition(
                    definition.name,
                    definition.description,
                    definition.handler,
                    definition.scope,
                    aliases,
                )
            )
        return cls(rebuilt)

    def resolve(self, command: str) -> ResolvedCommand | None:
        """Resolve ``/command`` or ``command`` to its canonical definition."""

        try:
            normalized = _normalize_name(command)
        except ValueError:
            return None
        definition = self._index.get(normalized)
        if definition is None:
            return None
        return ResolvedCommand(definition, normalized)

    def definitions(self, scope: CommandScope = CommandScope.PUBLIC) -> tuple[CommandDefinition, ...]:
        """Return canonical definitions visible in a Telegram command scope."""

        requested = CommandScope(scope)
        rank = _SCOPE_RANK[requested]
        return tuple(
            definition
            for definition in self._definitions
            if _SCOPE_RANK[definition.scope] <= rank
        )

    def menu_rows(
        self,
        scope: CommandScope = CommandScope.PUBLIC,
        *,
        limit: int | None = 100,
    ) -> list[tuple[str, str]]:
        """Return canonical ``(name, description)`` rows for BotCommand."""

        rows = [(item.name, item.description[:256]) for item in self.definitions(scope)]
        return rows if limit is None else rows[: max(0, int(limit))]

    def handler_bindings(self, *, include_aliases: bool = True) -> dict[str, str]:
        """Return command names to handler attribute names for registration."""

        bindings: dict[str, str] = {}
        for definition in self._definitions:
            bindings[definition.name] = definition.handler
            if include_aliases:
                bindings.update({alias: definition.handler for alias in definition.aliases})
        return bindings

    def names(self, *, include_aliases: bool = False) -> tuple[str, ...]:
        if include_aliases:
            return tuple(self._index)
        return tuple(item.name for item in self._definitions)

    def all_definitions(self) -> tuple[CommandDefinition, ...]:
        """Return every canonical definition, independent of menu scope.

        Telegram exposes a different command menu for each scope, but handler
        registration must see the complete catalogue.  Keeping this accessor
        explicit avoids reaching into the registry's private tuple from
        bootstrap code and makes it possible to merge the Arel catalogue with
        the rolling legacy command list safely.
        """

        return self._definitions


# The Arel command names are intentionally different from the names in the
# imported Guardian help text.  Existing names stay as hidden aliases during
# migration; handlers can be wired in later waves without changing the public
# menu again.  The registry is the canonical place for this decision.
_AREL_SPECS: tuple[CommandDefinition, ...] = (
    CommandDefinition("broadcastpin", "Duyuruyu t\u00fcm sohbetlere g\u00f6nder ve sabitle", "handle_broadcastpin_command", CommandScope.OWNER),
    CommandDefinition("arelmerkez", "Arel yönetim merkezi", "handle_admin_center_command", CommandScope.OWNER, ("panel",)),
    CommandDefinition("hizlikur", "Grubu adım adım kur", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("kurulum",)),
    CommandDefinition("kayitkanali", "Moderasyon kayıt kanalını ayarla", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("logkanal",)),
    CommandDefinition("grupayari", "Bu grubun Arel ayarları", "handle_ayar_command", CommandScope.GROUP_ADMIN, ("ayarlar",)),
    CommandDefinition("grupkurallari", "Grup kurallarını göster", "handle_guardian_command", CommandScope.PUBLIC, ("rules", "setrules", "kurallar", "chatkurallari")),
    CommandDefinition("sustur", "Bir üyeyi süreli sustur", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("sus", "mute")),
    CommandDefinition("tmute", "Bir üyeyi süreli sustur", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("gecicisustur",)),
    CommandDefinition("susturac", "Bir üyenin susturmasını kaldır", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("sesiac", "unmute")),
    CommandDefinition("yasakla", "Bir üyeyi gruptan uzaklaştır", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("ban",)),
    CommandDefinition("yasagiac", "Üyenin grup yasağını kaldır", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("unban",)),
    CommandDefinition("yetkiver", "Üyeye yönetici yetkisi ver", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("admin", "promote")),
    CommandDefinition("yetkial", "Yönetici yetkisini kaldır", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("deadmin", "demote")),
    CommandDefinition("yoneticiler", "Gruptaki yöneticileri listele", "handle_guardian_command", CommandScope.PUBLIC, ("adminler",)),
    CommandDefinition("yetkipaneli", "Yönetici yetkilerini düzenle", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("yetkiler",)),
    CommandDefinition("uyar", "Bir üyeye uyarı ver", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("warn",)),
    CommandDefinition("uyarlar", "Üyenin uyarılarını göster", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("warns",)),
    CommandDefinition("uyarisifirla", "Üyenin uyarılarını sıfırla", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("resetwarn",)),
    CommandDefinition("tekme", "Üyeyi gruptan çıkar", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("kick",)),
    CommandDefinition("geciciyasa", "Süreli grup yasağı uygula", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("tban",)),
    CommandDefinition("hayalet", "Gruba girmeden önce yasakla", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("hayaletban", "sban")),
    CommandDefinition("mesajisil", "Yanıtlanan mesajı sil", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("del",)),
    CommandDefinition("mesajtemizle", "Son mesajları topluca sil", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("purge", "temizle")),
    CommandDefinition("sabitle", "Yanıtlanan mesajı sabitle", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("pin",)),
    CommandDefinition("sabitlekaldir", "Mesaj sabitlemesini kaldır", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("unpin",)),
    CommandDefinition("rapor", "Mesajı yöneticilere raporla", "handle_guardian_command", CommandScope.PUBLIC, ("report",)),
    CommandDefinition("yasaklikelimeler", "Yasaklı kelimeleri yönet", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("yasakkelime", "blocklist")),
    CommandDefinition("iznilinkler", "İzinli alan adlarını yönet", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("guvenlilink", "whitelist")),
    CommandDefinition("linkkoruma", "Link korumasını aç veya kapat", "handle_guardian_command", CommandScope.GROUP_ADMIN),
    CommandDefinition("reklamkoruma", "Reklam korumasını aç veya kapat", "handle_guardian_command", CommandScope.GROUP_ADMIN),
    CommandDefinition("floodlimiti", "Flood eşiğini ayarla", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("floodlimit", "setflood")),
    CommandDefinition("floodcezasi", "Flood cezasını seç", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("floodmod", "setfloodmode")),
    CommandDefinition("raidkoruma", "Katılım saldırısı korumasını yönet", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("joinraidac", "raidbitti")),
    CommandDefinition("dogrulama", "Yeni üyeler için doğrulama ayarla", "handle_guardian_command", CommandScope.GROUP_ADMIN),
    CommandDefinition("hosgeldin", "Hoş geldin mesajını yönet", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("welcome",)),
    CommandDefinition("veda", "Veda mesajını yönet", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("goodbye",)),
    CommandDefinition("gecemodu", "Gece modunu yönet", "handle_guardian_command", CommandScope.GROUP_ADMIN),
    CommandDefinition("grupdili", "Grup dilini değiştir", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("dil", "language")),
    CommandDefinition("grupistatistik", "Grup istatistiklerini göster", "handle_group_analytics_command", CommandScope.GROUP_ADMIN, ("istatistik",)),
    CommandDefinition("sohbetmodu", "Arel sohbet modunu yönet", "handle_sohbet_command", CommandScope.GROUP_ADMIN, ("chatmod",)),
    CommandDefinition("areluslubu", "Arel üslubunu seç", "handle_personality_command", CommandScope.GROUP_ADMIN, ("stil",)),
    CommandDefinition("notekle", "Gruba not ekle", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("not_ekle",)),
    CommandDefinition("notsil", "Grup notunu sil", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("not_sil",)),
    CommandDefinition("filtre", "Kelime filtresi oluştur", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("filter",)),
    CommandDefinition("filtreler", "Aktif filtreleri listele", "handle_guardian_command", CommandScope.PUBLIC, ("filters",)),
    CommandDefinition("filtresil", "Kelime filtresini sil", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("stop",)),
    CommandDefinition("lock", "Bir medya türünü kilitle", "handle_guardian_command", CommandScope.GROUP_ADMIN),
    CommandDefinition("unlock", "Bir medya türünün kilidini aç", "handle_guardian_command", CommandScope.GROUP_ADMIN),
    CommandDefinition("locks", "Aktif medya kilitlerini göster", "handle_guardian_command", CommandScope.PUBLIC),
    CommandDefinition("oyunbitir", "Aktif oyunları bitir", "handle_oyunbitir_command", CommandScope.GROUP_ADMIN, ("oyundur", "oyunlardur")),
    CommandDefinition("trivia", "Bilgi yarışması başlat", "handle_quiz_command"),
    CommandDefinition("dogruluk", "Doğruluk turu başlat", "handle_truth_dare_command"),
    CommandDefinition("cesaret", "Cesaret turu başlat", "handle_truth_dare_command"),
    CommandDefinition("agkur", "Federasyon ağı oluştur", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("newfed", "fed_olustur")),
    CommandDefinition("agaekle", "Grubu federasyona bağla", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("joinfed", "fed_katil")),
    CommandDefinition("agdanayril", "Federasyondan ayrıl", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("leavefed",)),
    CommandDefinition("agdurum", "Federasyon durumunu göster", "handle_guardian_command", CommandScope.PUBLIC, ("fedstat",)),
    CommandDefinition("agban", "Federasyon yasağı uygula", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("fban",)),
    CommandDefinition("agbanac", "Federasyon yasağını kaldır", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("unfban", "funban")),
    CommandDefinition("agbanlar", "Federasyon yasaklarını listele", "handle_guardian_command", CommandScope.PUBLIC, ("fedbanlist",)),
    CommandDefinition("golgeban", "Gölge yasağı uygula", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("shadowban",)),
    CommandDefinition("golgeac", "Gölge yasağı kaldır", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("unshadowban", "unsban")),
    CommandDefinition("risk", "Kullanıcı risk kartını göster", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("whois",)),
    CommandDefinition("riskraporu", "Federasyon risk raporu", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("analytics",)),
    CommandDefinition("guvenliliste", "Güvenilir kullanıcıları yönet", "handle_guardian_command", CommandScope.GROUP_ADMIN, ("fedapprove", "fedunapprove")),
)


def build_arel_command_registry() -> CommandRegistry:
    """Return the canonical Arel/Guardian migration catalogue.

    Guardian's upstream bot has a larger command surface than the original
    Arel migration rows.  Add every still-unclaimed Guardian command here so
    it is registered exactly once and reaches ``handle_guardian_command``.
    Existing Arel rows deliberately win for commands that already have a
    tested first-party handler; their Guardian spelling remains a callable
    alias and is published by the menu adapter.
    """

    definitions = list(_AREL_SPECS)
    claimed = {name for item in definitions for name in (item.name, *item.aliases)}
    extra_public = {
        "start", "help", "info", "id", "echo", "afk", "notes", "get", "rules", "filters",
        "locks", "approved", "fedinfo", "myfed", "fedadmins", "fedstat", "userinfo", "firstmsg",
        "memberlog", "stats", "disabled",
    }
    # ``mod`` is still the backwards-compatible alias of Arel's
    # ``moderasyon`` summary.  Keep that definition so the administrator menu
    # does not disappear when the expanded Guardian catalogue is merged.
    skip_first_party = {"start", "help", "clear", "broadcast", "broadcastpin", "mod"}
    descriptions = {
        "start": "Arel'i başlatır", "help": "Guardian komutlarını gösterir", "info": "Sohbet ve kullanıcı kimliğini gösterir",
        "id": "Sohbet ve kullanıcı kimliğini gösterir", "echo": "Metni geri gönderir", "afk": "Geçici olarak uzakta olduğunu bildirir",
        "notes": "Guardian grup notlarını listeler", "save": "Guardian grup notu kaydeder", "get": "Guardian grup notunu getirir",
        "antilink": "Bağlantı korumasını açar veya kapatır", "antibotlink": "Bot bağlantısı korumasını yönetir",
        "antiraid": "Katılım saldırısı korumasını yönetir", "approve": "Kullanıcıyı onaylı listeye alır",
        "disapprove": "Kullanıcıyı onaylı listeden çıkarır", "approved": "Onaylı kullanıcıları listeler",
        "addblock": "Kara listeye kelime ekler", "rmblock": "Kara listeden kelime siler", "blocklistmode": "Kara liste cezasını seçer",
        "captchatype": "Doğrulama tipini seçer", "captcha": "Yeni üyeler için doğrulamayı yönetir",
        "cleanservice": "Telegram servis mesajlarını temizler", "connect": "Bağlı sohbet ayarını yönetir",
        "disconnect": "Bağlı sohbet ayarını kaldırır", "export": "Guardian ayarlarını dışa aktarır", "import": "Guardian ayarlarını içe alır",
        "disable": "Bir komutu bu grupta kapatır", "enable": "Kapatılmış komutu açar", "disabled": "Kapalı komutları listeler",
        "fsub": "Zorunlu kanal aboneliğini yönetir", "gban": "Kullanıcıyı genel yasak kaydına alır", "ungban": "Genel yasağı kaldırır",
        "setwelcome": "Karşılama mesajını ayarlar", "setgoodbye": "Veda mesajını ayarlar", "cleanwelcome": "Karşılama temizliğini yönetir",
        "welcomemute": "Karşılama susturmasını yönetir", "lockmode": "Medya kilidi cezasını seçer", "logchannel": "Kayıt kanalını ayarlar",
        "mban": "Medya türünü yasaklar", "munban": "Medya yasağını kaldırır", "nightmode": "Gece modunu yönetir", "nighttime": "Gece saatlerini ayarlar",
        "setwarnlimit": "Uyarı sınırını ayarlar", "setwarnmode": "Uyarı cezasını seçer", "recurring": "Tekrarlı mesaj planlar", "stoprecurring": "Tekrarlı mesajı durdurur",
        "alias": "Grup komut takma adı ekler", "delalias": "Grup komut takma adını siler", "aliases": "Grup takma adlarını listeler",
        "mod": "Kullanıcıya Guardian moderatör rolü verir", "unmod": "Guardian moderatör rolünü kaldırır", "free": "Kullanıcıyı koruma istisnasına alır",
        "unfree": "Koruma istisnasını kaldırır", "staff": "Grup ekibini listeler", "fsub": "Zorunlu kanal aboneliğini yönetir",
        "userinfo": "Kullanıcı bilgi kartını gösterir", "firstmsg": "Kullanıcının ilk mesajını gösterir", "memberlog": "Üye geçmişini listeler",
        "zombies": "Uzun süredir pasif üyeleri listeler", "reload": "Guardian ayarlarını yeniden yükler", "stats": "Guardian durumunu gösterir",
        "setrules": "Grup kurallarını kaydeder", "rules": "Grup kurallarını gösterir", "privaterules": "Özel kural mesajını yönetir",
        "setflood": "Flood eşiğini ayarlar", "setfloodmode": "Flood cezasını seçer", "lock": "Medya türünü kilitler", "unlock": "Medya kilidini kaldırır", "locks": "Aktif medya kilitlerini listeler",
    }
    from src.moderation.guardian_surface import GuardianSurface

    for command in GuardianSurface.COMMANDS:
        command = str(command).casefold()
        if command in claimed or command in skip_first_party:
            continue
        scope = CommandScope.PUBLIC if command in extra_public else CommandScope.GROUP_ADMIN
        definitions.append(
            CommandDefinition(
                command,
                descriptions.get(command, f"Guardian {command} komutu"),
                "handle_guardian_command",
                scope,
            )
        )
        claimed.add(command)
    return CommandRegistry(definitions)


__all__ = [
    "CommandDefinition",
    "CommandRegistry",
    "CommandScope",
    "EXPLICIT_HANDLER_MAP",
    "LEGACY_ALIAS_MAP",
    "ResolvedCommand",
    "build_arel_command_registry",
]
