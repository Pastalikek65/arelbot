from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from main import (
    GROUP_ADMIN_COMMANDS,
    MENU_ALIAS_COMMANDS,
    OWNER_ONLY_COMMANDS,
    TELEGRAM_COMMAND_LIMIT,
    _build_runtime_command_registry,
    build_bot_command_menu,
    register_handlers,
    register_bot_commands,
)
from src.telegram.command_registry import (
    CommandRegistry,
    CommandScope,
    EXPLICIT_HANDLER_MAP,
    LEGACY_ALIAS_MAP,
)
from main import AREL_HANDLER_FALLBACKS
from telegram.ext import CommandHandler


def _declared_commands() -> list[tuple[str, str]]:
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name != "register_bot_commands":
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "commands" for target in child.targets
            ):
                return ast.literal_eval(child.value)
    raise AssertionError("register_bot_commands command list missing")


def test_telegram_command_menu_respects_api_limit_and_keeps_canonical_features():
    declared = _declared_commands()
    menu = build_bot_command_menu(declared)
    names = [name for name, _description in menu]

    assert len(menu) <= TELEGRAM_COMMAND_LIMIT
    assert len(names) == len(set(names))
    assert "start" in names
    assert "hava" in names
    assert "sehir" in names
    assert "dl" not in names
    assert "download" not in names
    # Guardian controls are callable compatibility commands, but their
    # discoverability lives in the button-based Guardian help center.
    assert not set(names).intersection({"ban", "mute", "rules", "welcome", "captcha"})
    assert all(len(description) <= 256 for _name, description in menu)


def test_public_command_menu_excludes_owner_operational_commands():
    declared = _declared_commands()
    names = {name for name, _description in build_bot_command_menu(
        declared, excluded_commands=OWNER_ONLY_COMMANDS
    )}
    assert not names.intersection(OWNER_ONLY_COMMANDS)
    assert "hava" in names


def test_public_command_menu_hides_group_administrator_commands():
    declared = _declared_commands()
    names = {name for name, _description in build_bot_command_menu(declared)}

    assert "istatistik" not in names
    assert "moderasyon" not in names
    assert "ayar" not in names


def test_group_administrator_scope_keeps_public_commands_and_adds_admin_commands():
    registry = CommandRegistry.from_rows(
        _declared_commands(),
        hidden_aliases=MENU_ALIAS_COMMANDS,
        hidden_alias_map=LEGACY_ALIAS_MAP,
        group_admin_commands=GROUP_ADMIN_COMMANDS,
        owner_commands=OWNER_ONLY_COMMANDS,
    )
    names = {name for name, _description in registry.menu_rows(CommandScope.GROUP_ADMIN)}

    assert "hava" in names
    assert "istatistik" in names
    assert "moderasyon" in names
    assert "mod" not in names


@pytest.mark.asyncio
async def test_register_bot_commands_publishes_scope_specific_menus(monkeypatch):
    class Bot:
        def __init__(self):
            self.calls = []

        async def set_my_commands(self, commands, scope=None):
            self.calls.append((commands, scope))

    bot = Bot()
    monkeypatch.setattr("main.Config.OWNER_ID", 123)
    await register_bot_commands(SimpleNamespace(bot=bot))

    assert len(bot.calls) == 4
    assert bot.calls[0][1] is None
    assert type(bot.calls[1][1]).__name__ == "BotCommandScopeAllPrivateChats"
    assert type(bot.calls[2][1]).__name__ == "BotCommandScopeAllChatAdministrators"
    assert type(bot.calls[3][1]).__name__ == "BotCommandScopeChat"
    public_names = {item.command for item in bot.calls[0][0]}
    admin_names = {item.command for item in bot.calls[2][0]}
    owner_names = {item.command for item in bot.calls[3][0]}
    assert "moderasyon" not in public_names
    assert "moderasyon" in admin_names
    assert "hizlikur" not in admin_names
    assert not admin_names.intersection({"ban", "mute", "rules", "welcome", "captcha"})
    assert "kurulum" not in admin_names
    assert "arelmerkez" in owner_names
    assert OWNER_ONLY_COMMANDS.issubset(owner_names)


def test_register_handlers_exposes_arel_commands_and_hidden_aliases():
    """Every canonical Arel row is callable while aliases stay out of menus."""

    class Application:
        def __init__(self):
            self.handlers = []
            self.errors = []

        def add_handler(self, handler):
            self.handlers.append(handler)

        def add_error_handler(self, handler):
            self.errors.append(handler)

    class Orchestrator:
        def __getattr__(self, name):
            async def _handler(update, context):
                return None

            return _handler

    application = Application()
    register_handlers(application, Orchestrator())

    command_names = {
        name
        for handler in application.handlers
        if isinstance(handler, CommandHandler)
        for name in handler.commands
    }
    assert {"hizlikur", "grupayari", "yasakla", "arelmerkez"}.issubset(command_names)
    assert {"kurulum", "ayarlar", "ban", "panel"}.issubset(command_names)
    # The deduplicating registrar must not install two competing callbacks for
    # one Telegram command during the rolling migration.
    assert len(command_names) == sum(
        len(handler.commands)
        for handler in application.handlers
        if isinstance(handler, CommandHandler)
    )


def test_guardian_ban_alias_does_not_fall_back_to_legacy_owner_handler():
    """The public /ban alias must enter the typed Guardian dispatcher."""

    class Application:
        def __init__(self):
            self.handlers = []

        def add_handler(self, handler):
            self.handlers.append(handler)

        def add_error_handler(self, handler):
            return None

    class Orchestrator:
        async def handle_guardian_command(self, update, context):
            return None

        async def handle_ban_command(self, update, context):
            return None

        def __getattr__(self, name):
            async def _handler(update, context):
                return None

            return _handler

    application = Application()
    orchestrator = Orchestrator()
    register_handlers(application, orchestrator)
    ban_handler = next(
        item for item in application.handlers
        if isinstance(item, CommandHandler) and "ban" in item.commands
    )
    assert ban_handler.callback.__func__ is orchestrator.handle_guardian_command.__func__


def test_unimplemented_canonical_row_uses_explicit_arel_fallback_for_legacy_surface():
    """A migration placeholder must not steal an already working command alias."""

    class PartialOrchestrator:
        async def handle_sohbet_command(self, update, context):
            return None

        def __getattr__(self, name):
            if name == "handle_chat_mode_command":
                raise AttributeError(name)

            async def _handler(update, context):
                return None

            return _handler

    class Application:
        def __init__(self):
            self.handlers = []

        def add_handler(self, handler):
            self.handlers.append(handler)

        def add_error_handler(self, handler):
            return None

    application = Application()
    orchestrator = PartialOrchestrator()
    register_handlers(application, orchestrator)

    sohbet_handler = next(
        item
        for item in application.handlers
        if isinstance(item, CommandHandler) and "sohbetmodu" in item.commands
    )
    assert sohbet_handler.callback.__func__ is orchestrator.handle_sohbet_command.__func__
    assert AREL_HANDLER_FALLBACKS["sohbetmodu"] == "handle_sohbet_command"


def test_runtime_registry_uses_only_explicit_semantic_bindings():
    """Feature commands resolve to their existing typed handlers.

    This guards the rolling migration against name-based guessing.  A command
    absent from the explicit map is allowed to retain its catalogue handler or
    honest unavailable placeholder, but mapped public features must never
    drift to a similarly named method.
    """

    registry = _build_runtime_command_registry(_declared_commands())
    expected = {
        "hava": "handle_hava_command",
        "deprem": "handle_deprem_command",
        "haber": "handle_haber_command",
        "eczane": "handle_eczane_command",
        "akaryakit": "handle_akaryakit_command",
        "doviz": "handle_doviz_command",
        "altin": "handle_doviz_command",
        "bim": "handle_bim_command",
        "ucak": "handle_ucak_command",
        "ezan": "handle_ezan_command",
        "sehir": "handle_city_command",
        "not": "handle_note_command",
        "gorev": "handle_task_command",
        "remind": "handle_remind_command",
        "q": "handle_q_command",
        "qrand": "handle_qrand_command",
        "sticker": "handle_sticker_command",
        "packs": "handle_packs_command",
        "adamasmaca": "handle_hangman_command",
        "kelimeoyunu": "handle_wordgame_command",
    }
    for command, handler in expected.items():
        resolved = registry.resolve(command)
        assert resolved is not None, command
        assert resolved.handler == handler, command
        assert EXPLICIT_HANDLER_MAP[command] == handler


def test_guardian_only_catalogue_entries_are_not_mapped_to_public_features():
    """Unknown Guardian operations keep an honest service boundary."""

    registry = _build_runtime_command_registry(_declared_commands())
    for command in ("hizlikur", "kayitkanali", "yetkiver", "agkur", "riskraporu"):
        resolved = registry.resolve(command)
        assert resolved is not None
        assert command not in EXPLICIT_HANDLER_MAP
        assert resolved.handler.startswith("handle_")

