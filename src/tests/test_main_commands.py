from __future__ import annotations

import ast
from pathlib import Path

from main import OWNER_ONLY_COMMANDS, TELEGRAM_COMMAND_LIMIT, build_bot_command_menu


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
    assert all(len(description) <= 256 for _name, description in menu)


def test_public_command_menu_excludes_owner_operational_commands():
    declared = _declared_commands()
    names = {name for name, _description in build_bot_command_menu(
        declared, excluded_commands=OWNER_ONLY_COMMANDS
    )}
    assert not names.intersection(OWNER_ONLY_COMMANDS)
    assert "hava" in names

