"""Replay every published and compatibility command through PTB routing."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from telegram import Bot, Update, User
from telegram.ext import CommandHandler as TelegramCommandHandler

from main import _build_runtime_command_registry, register_handlers
from src.moderation.guardian_surface import GuardianSurface
from src.orchestrator import Orchestrator


def _declared_rows():
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "register_bot_commands":
            for child in ast.walk(node):
                if isinstance(child, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "commands" for target in child.targets
                ):
                    return ast.literal_eval(child.value)
    raise AssertionError("command_menu_missing")


class _Application:
    def __init__(self):
        self.handlers = []

    def add_handler(self, handler):
        self.handlers.append(handler)

    def add_error_handler(self, handler):
        pass


class _RouteRecorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if not hasattr(Orchestrator, name):
            raise AttributeError(name)

        async def callback(update, context):
            self.calls.append((name, update.effective_message.text))

        callback.__name__ = name
        return callback


def _routes():
    app = _Application()
    recorder = _RouteRecorder()
    register_handlers(app, recorder)
    routes = {}
    for handler in app.handlers:
        if not isinstance(handler, TelegramCommandHandler):
            continue
        for command in handler.commands:
            assert command not in routes, f"duplicate_command:{command}"
            routes[command] = handler
    return app, recorder, routes


_APP, _RECORDER, _ROUTES = _routes()
_COMMANDS = sorted(_ROUTES)
_BOT = Bot("123456:replay-only-token")
_BOT._bot_user = User(id=123456, is_bot=True, first_name="Arel", username="ArelChat_Bot")


def test_registered_commands_cover_the_independent_catalogs_without_placeholders():
    registry = _build_runtime_command_registry(_declared_rows())
    required = {name for definition in registry.all_definitions() for name in (definition.name, *definition.aliases)}
    required.update(GuardianSurface.COMMANDS)
    assert required <= set(_COMMANDS)
    assert all(handler.callback.__name__ != "_handler" for handler in _ROUTES.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("command", _COMMANDS)
async def test_each_command_matches_and_invokes_its_registered_callback(command):
    text = f"/{command}@ArelChat_Bot"
    update = Update.de_json({
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 1760700000,
            "chat": {"id": -1009990001, "type": "supergroup", "title": "Command replay"},
            "from": {"id": 9990001, "is_bot": False, "first_name": "Test"},
            "text": text,
            "entities": [{"offset": 0, "length": len(text), "type": "bot_command"}],
        },
    }, _BOT)
    matches = [
        handler for handler in _APP.handlers
        if isinstance(handler, TelegramCommandHandler) and handler.check_update(update)
    ]
    assert len(matches) == 1, f"{command}: {[h.commands for h in matches]}"
    handler = matches[0]
    assert handler is _ROUTES[command]
    previous = len(_RECORDER.calls)
    await handler.callback(update, object())
    assert _RECORDER.calls[previous:] == [(handler.callback.__name__, text)]
