from __future__ import annotations

from pathlib import Path

import scripts.architecture_check as architecture_check


def test_architecture_check_rejects_handler_database_attribute(tmp_path: Path, monkeypatch):
    handler_dir = tmp_path / "handlers"
    handler_dir.mkdir()
    (handler_dir / "bad_handler.py").write_text(
        "class Bad:\n    async def run(self):\n        return self.chat_engine.db.get_user(1)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(architecture_check, "HANDLER_DIR", handler_dir)

    errors = architecture_check.check_handlers()

    assert any("database access" in error for error in errors)


def test_architecture_check_rejects_orchestrator_database_attribute(tmp_path: Path, monkeypatch):
    orchestrator = tmp_path / "orchestrator.py"
    orchestrator.write_text(
        "class Orchestrator:\n"
        "    async def handle_admin(self):\n"
        "        return self.chat_engine.db.get_audit_logs()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(architecture_check, "ORCHESTRATOR_PATH", orchestrator)

    errors = architecture_check.check_orchestrator_boundaries()

    assert any("orchestrator database access" in error for error in errors)


def test_architecture_check_rejects_raw_exception_interpolation(tmp_path: Path, monkeypatch):
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "bad_logging.py").write_text(
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "def run():\n"
        "    try:\n"
        "        raise RuntimeError('secret')\n"
        "    except Exception as exc:\n"
        "        logger.error(f'failed: {exc}')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(architecture_check, "ROOT", tmp_path)

    errors = architecture_check.check_exception_logging()

    assert errors == ["raw exception interpolation in src/bad_logging.py:7"]


def test_architecture_check_rejects_mojibake_in_markdown(tmp_path: Path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    # A Turkish UTF-8 sentence decoded once as cp1254 must be rejected before
    # it reaches the documentation tree.
    broken = (
        "Bootstrap dispatch'i ayn\u00c4\u00b1 s\u00c3\u00b6zle\u00c5\u0178meyi "
        "kullan\u00c4\u00b1r.\n"
    )
    (docs / "broken.md").write_text(broken, encoding="utf-8")
    (docs / "good.md").write_text("Bootstrap dispatch'i aynı sözleşmeyi kullanır.\n", encoding="utf-8")
    monkeypatch.setattr(architecture_check, "ROOT", tmp_path)

    errors = architecture_check.check_document_encoding()

    assert errors == ["possible mojibake in documentation: docs/broken.md"]


def test_architecture_check_does_not_exempt_chat_engine_from_encoding_guard(tmp_path: Path, monkeypatch):
    source = tmp_path / "src"
    source.mkdir()
    broken = "label = " + repr(chr(0xC3) + chr(0xA7)) + "\n"
    (source / "chat_engine.py").write_text(broken, encoding="utf-8")
    monkeypatch.setattr(architecture_check, "ROOT", tmp_path)

    errors = architecture_check.check_encoding()

    assert errors == ["possible mojibake: src/chat_engine.py"]


def test_architecture_check_rejects_legacy_media_http_bypass(tmp_path: Path, monkeypatch):
    source = tmp_path / "src"
    source.mkdir()
    (source / "video_downloader.py").write_text(
        "import httpx\nCOBALT = 'https://api.cobalt.tools'\n",
        encoding="utf-8",
    )
    (source / "tiktok_downloader.py").write_text("# no direct network\n", encoding="utf-8")
    monkeypatch.setattr(architecture_check, "ROOT", tmp_path)

    errors = architecture_check.check_legacy_media_facades()

    assert any("typed provider boundary" in error for error in errors)


def test_architecture_check_rejects_module_level_orchestrator(tmp_path: Path, monkeypatch):
    main = tmp_path / "main.py"
    main.write_text(
        "from src.orchestrator import Orchestrator\n"
        "orchestrator = Orchestrator()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(architecture_check, "MAIN_PATH", main)

    errors = architecture_check.check_main_bootstrap()

    assert errors == ["main.py must instantiate Orchestrator inside __main__"]


def test_command_surface_requires_manifest_commands_in_handler_and_menu(tmp_path: Path, monkeypatch):
    source = tmp_path / "src"
    features = source / "features"
    features.mkdir(parents=True)
    (source / "chat_engine.py").write_text(
        "FeatureDefinition(commands=('ready', 'missing_handler', 'missing_menu'))\n",
        encoding="utf-8",
    )
    (features / "catalog.py").write_text(
        "SPATULA_FEATURES = (('city', 'turkiye', ('missing_handler', 'catalog_command'), 'tool'),)\n",
        encoding="utf-8",
    )
    main = tmp_path / "main.py"
    main.write_text(
        "commands = [('ready', 'visible')]\n"
        "CommandHandler('ready', callback)\n"
        "CommandHandler('missing_menu', callback)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(architecture_check, "ROOT", tmp_path)
    monkeypatch.setattr(architecture_check, "MAIN_PATH", main)

    errors = architecture_check.check_command_surface()

    assert errors == [
        "feature command has no Telegram handler: /catalog_command",
        "feature command has no Telegram handler: /missing_handler",
        "feature command missing from BotCommand menu: /catalog_command",
        "feature command missing from BotCommand menu: /missing_handler",
        "feature command missing from BotCommand menu: /missing_menu",
    ]
