from __future__ import annotations

import json
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


def _write_database_module(root: Path) -> None:
    """Write the database facade the service check reads the collection names from.

    The check learns the Mongo collection names from ``src/database.py``
    instead of a word list, so a fixture repository has to publish them the
    same way the real module does.
    """

    database = root / "src" / "database.py"
    database.parent.mkdir(parents=True, exist_ok=True)
    database.write_text(
        "class Database:\n"
        "    _db = None\n"
        "    @property\n"
        '    def notes(self): return Database._db["notes"]\n'
        "    @property\n"
        '    def economy_accounts(self): return Database._db["economy_accounts"]\n',
        encoding="utf-8",
    )


def _write_service(root: Path, name: str, body: str) -> None:
    service = root / "src" / "services" / name
    service.parent.mkdir(parents=True, exist_ok=True)
    service.write_text(body, encoding="utf-8")


def test_check_service_persistence_flags_raw_collection_use(tmp_path: Path):
    _write_database_module(tmp_path)
    _write_service(
        tmp_path,
        "bad_service.py",
        "class S:\n    async def go(self):\n        await self.db.notes.insert_one({})\n",
    )

    errors = architecture_check.check_service_persistence(tmp_path)

    assert errors == ["src/services/bad_service.py:3: service builds a raw Mongo collection"]


def test_check_service_persistence_accepts_store_usage(tmp_path: Path):
    _write_database_module(tmp_path)
    _write_service(
        tmp_path,
        "good_service.py",
        "class S:\n    async def go(self):\n        await self.store.insert({})\n",
    )

    assert architecture_check.check_service_persistence(tmp_path) == []


def test_check_service_persistence_allows_repository_port_calls(tmp_path: Path):
    """A port method call on the database facade is not a raw collection."""

    _write_database_module(tmp_path)
    _write_service(
        tmp_path,
        "leaderboard_service.py",
        "class S:\n    async def go(self):\n        return await self.db.get_leaderboard(1)\n",
    )

    assert architecture_check.check_service_persistence(tmp_path) == []


def test_check_service_persistence_flags_dynamic_collection_lookup(tmp_path: Path):
    """A dynamic lookup must not become a way around the guard."""

    _write_database_module(tmp_path)
    _write_service(
        tmp_path,
        "sneaky_service.py",
        "class S:\n    def go(self):\n        return getattr(self.db, \"notes\", None)\n",
    )

    errors = architecture_check.check_service_persistence(tmp_path)

    assert errors == ["src/services/sneaky_service.py:3: service builds a raw Mongo collection"]


def test_check_service_persistence_flags_alternate_facade_attribute(tmp_path: Path):
    """The facade attribute name does not matter, the collection does."""

    _write_database_module(tmp_path)
    _write_service(
        tmp_path,
        "renamed_service.py",
        "class S:\n    async def go(self):\n        await self.database.notes.insert_one({})\n",
    )

    errors = architecture_check.check_service_persistence(tmp_path)

    assert errors == ["src/services/renamed_service.py:3: service builds a raw Mongo collection"]


def test_check_service_persistence_flags_collection_subscript(tmp_path: Path):
    """A collection read by key is a raw collection as well."""

    _write_database_module(tmp_path)
    _write_service(
        tmp_path,
        "subscript_service.py",
        "class S:\n    async def go(self):\n        await self.db[\"notes\"].insert_one({})\n",
    )

    errors = architecture_check.check_service_persistence(tmp_path)

    assert errors == ["src/services/subscript_service.py:3: service builds a raw Mongo collection"]


def test_check_service_persistence_requires_the_database_module(tmp_path: Path):
    """Without the collection names the guard must not report a clean service."""

    _write_service(
        tmp_path,
        "good_service.py",
        "class S:\n    async def go(self):\n        await self.store.insert({})\n",
    )

    errors = architecture_check.check_service_persistence(tmp_path)

    assert errors == ["src/database.py is missing: the Mongo collection names cannot be read"]


def test_check_secret_patterns_flags_provider_key(tmp_path: Path):
    target = tmp_path / "src" / "x.py"
    target.parent.mkdir(parents=True)
    target.write_text('KEY = "gsk_abcdefghijklmnopqrstuvwxyz012345"\n', encoding="utf-8")

    errors = architecture_check.check_secret_patterns(tmp_path)

    assert errors == ["src/x.py:1: possible hard-coded secret (groq-key)"]


def test_check_secret_patterns_flags_named_assignment_secrets(tmp_path: Path):
    """The assignment names the shared secret guard does not recognise."""

    target = tmp_path / "src" / "x.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        'ENCRYPTION_KEY = "0123456789abcdef"\n'
        'GROQ_KEYS = "not-a-gsk-value-at-all"\n'
        'AUTH_TOKEN = "not-a-telegram-token"\n',
        encoding="utf-8",
    )

    errors = architecture_check.check_secret_patterns(tmp_path)

    assert errors == [
        "src/x.py:1: possible hard-coded secret (credential-assignment)",
        "src/x.py:2: possible hard-coded secret (credential-assignment)",
        "src/x.py:3: possible hard-coded secret (credential-assignment)",
    ]
    assert all("0123456789abcdef" not in error for error in errors)
    assert all("not-a-gsk-value-at-all" not in error for error in errors)
    assert all("not-a-telegram-token" not in error for error in errors)


def test_check_secret_patterns_leaves_the_repo_wide_assignment_family_alone(tmp_path: Path):
    """``check_secrets`` reports these names repo-wide, so do not repeat them.

    The assignment name is assembled at runtime: a literal assignment in this
    file would itself be reported by ``check_secrets``, which scans the test
    tree as well.
    """

    target = tmp_path / "src" / "cfg.py"
    target.parent.mkdir(parents=True)
    name = "webhook" + "_secret"
    target.write_text(f'{name} = "0123456789abcdef"\n', encoding="utf-8")

    assert architecture_check.check_secret_patterns(tmp_path) == []


def test_check_secret_patterns_reports_location_without_the_secret_value(tmp_path: Path):
    leaked = "gsk_abcdefghijklmnopqrstuvwxyz012345"
    target = tmp_path / "src" / "x.py"
    target.parent.mkdir(parents=True)
    target.write_text(f'KEY = "{leaked}"\n', encoding="utf-8")

    errors = architecture_check.check_secret_patterns(tmp_path)

    assert errors
    assert all(leaked not in error for error in errors)


def test_check_secret_patterns_ignores_test_fixtures(tmp_path: Path):
    """Test fixtures model leaking secrets and must not break the gate."""

    tests = tmp_path / "src" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_security.py").write_text(
        'KEY = "gsk_abcdefghijklmnopqrstuvwxyz012345"\n', encoding="utf-8"
    )

    assert architecture_check.check_secret_patterns(tmp_path) == []


def test_check_secret_patterns_flags_connection_string_credentials(tmp_path: Path):
    target = tmp_path / "config.py"
    target.write_text('URI = "mongodb://user:pass@localhost/arel_bot"\n', encoding="utf-8")

    errors = architecture_check.check_secret_patterns(tmp_path)

    assert errors == ["config.py:1: possible hard-coded secret (connection-uri)"]


def test_check_secret_patterns_scans_scripts_and_entrypoints(tmp_path: Path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "tool.py").write_text('KEY = "AIza%s"\n' % ("b" * 35), encoding="utf-8")
    (tmp_path / "main.py").write_text('KEY = "tvly-%s"\n' % ("c" * 25), encoding="utf-8")

    errors = architecture_check.check_secret_patterns(tmp_path)

    assert errors == [
        "main.py:1: possible hard-coded secret (tavily-key)",
        "scripts/tool.py:1: possible hard-coded secret (gemini-key)",
    ]


def test_check_license_registry_flags_unregistered_host(tmp_path: Path):
    provider = tmp_path / "src" / "features" / "providers" / "p.py"
    provider.parent.mkdir(parents=True)
    provider.write_text('URL = "https://unregistered-source.example/x"\n', encoding="utf-8")
    (tmp_path / "licenses.json").write_text(
        '{"sources": [{"name": "known", "host": "known.example"}]}', encoding="utf-8"
    )

    errors = architecture_check.check_license_registry(tmp_path)

    assert errors == [
        "src/features/providers/p.py:1: provider host is not registered in licenses.json "
        "(unregistered-source.example)"
    ]


def test_check_license_registry_accepts_registered_host(tmp_path: Path):
    provider = tmp_path / "src" / "features" / "providers" / "p.py"
    provider.parent.mkdir(parents=True)
    provider.write_text('URL = "https://known.example/x"\n', encoding="utf-8")
    (tmp_path / "licenses.json").write_text(
        '{"sources": [{"name": "known", "host": "known.example"}]}', encoding="utf-8"
    )

    assert architecture_check.check_license_registry(tmp_path) == []


def test_check_license_registry_accepts_subdomain_of_registered_host(tmp_path: Path):
    provider = tmp_path / "src" / "media" / "providers" / "p.py"
    provider.parent.mkdir(parents=True)
    provider.write_text('URL = "https://api.known.example/x"\n', encoding="utf-8")
    (tmp_path / "licenses.json").write_text(
        '{"sources": [{"name": "known", "host": "known.example"}]}', encoding="utf-8"
    )

    assert architecture_check.check_license_registry(tmp_path) == []


def test_check_license_registry_accepts_registry_entry_written_as_url(tmp_path: Path):
    """A registry entry may carry a scheme or path without losing its host."""

    provider = tmp_path / "src" / "features" / "providers" / "p.py"
    provider.parent.mkdir(parents=True)
    provider.write_text('URL = "https://www.known.example/x"\n', encoding="utf-8")
    (tmp_path / "licenses.json").write_text(
        '{"sources": [{"name": "known", "host": "https://known.example/"}]}', encoding="utf-8"
    )

    assert architecture_check.check_license_registry(tmp_path) == []


def test_check_license_registry_derives_the_host_from_the_registry_url(tmp_path: Path):
    """A record without an explicit host still names its source."""

    provider = tmp_path / "src" / "features" / "providers" / "p.py"
    provider.parent.mkdir(parents=True)
    provider.write_text('URL = "https://known.example/x"\n', encoding="utf-8")
    (tmp_path / "licenses.json").write_text(
        '{"sources": [{"name": "known", "url": "https://known.example/reference"}]}', encoding="utf-8"
    )

    assert architecture_check.check_license_registry(tmp_path) == []


def test_check_license_registry_flags_an_unregistered_bare_host(tmp_path: Path):
    """A host allowlist carries no URL, so it needs its own registry pass."""

    provider = tmp_path / "src" / "features" / "providers" / "lyrics.py"
    provider.parent.mkdir(parents=True)
    provider.write_text(
        "allowed_hosts = frozenset({\n"
        '    "known-source.com",\n'
        '    "missing-lyrics.com",\n'
        "})\n",
        encoding="utf-8",
    )
    (tmp_path / "licenses.json").write_text(
        '{"sources": [{"name": "known", "host": "known-source.com"}]}', encoding="utf-8"
    )

    errors = architecture_check.check_license_registry(tmp_path)

    assert errors == [
        "src/features/providers/lyrics.py:1: provider host is not registered in "
        "licenses.json (missing-lyrics.com)"
    ]


def test_check_license_registry_ignores_strings_that_are_not_hosts(tmp_path: Path):
    """Module names, selectors, file names and mapping aliases are not hosts."""

    provider = tmp_path / "src" / "features" / "providers" / "p.py"
    provider.parent.mkdir(parents=True)
    provider.write_text(
        "PARSER = 'html.parser'\n"
        "SELECTORS = {'div.col-md-6', 'article.nearest-pharmacy-card'}\n"
        "FILES = ['media.mp4', 'admin.cookies.txt']\n"
        "LOCAL = 'localhost.localdomain'\n"
        "ALIASES = {'thy.is': 'THYAO.IS', 'thy': 'THYAO.IS'}\n",
        encoding="utf-8",
    )
    (tmp_path / "licenses.json").write_text('{"sources": []}', encoding="utf-8")

    assert architecture_check.check_license_registry(tmp_path) == []


def test_check_license_registry_flags_the_shipped_lyrics_allowlist(tmp_path: Path):
    """The real lyrics allowlist must be caught while it is unregistered."""

    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "features"
        / "providers"
        / "lyrics.py"
    )
    provider = tmp_path / "src" / "features" / "providers" / "lyrics.py"
    provider.parent.mkdir(parents=True)
    provider.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "licenses.json").write_text(
        '{"sources": [{"name": "genius", "host": "genius.com"}]}', encoding="utf-8"
    )

    errors = architecture_check.check_license_registry(tmp_path)

    assert {
        error.rsplit("(", 1)[-1].rstrip(")") for error in errors
    } == {"musixmatch.com", "lyrics.com", "azlyrics.com", "songlyrics.com"}


def test_check_document_encoding_ignores_a_uri_query_string(tmp_path: Path, monkeypatch):
    """A connection string carries a query string, not a lost Turkish letter."""

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "connection.md").write_text(
        "Baglanti: mongodb://mongo:27017/arel_bot_test?authSource=admin\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(architecture_check, "ROOT", tmp_path)

    assert architecture_check.check_document_encoding() == []


def test_check_document_encoding_reports_a_lost_turkish_letter(tmp_path: Path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "broken.md").write_text("d?ner kurali\n", encoding="utf-8")
    monkeypatch.setattr(architecture_check, "ROOT", tmp_path)

    errors = architecture_check.check_document_encoding()

    assert errors == ["possible lost character in documentation: docs/broken.md"]


def test_check_document_encoding_skips_gitignored_directories(tmp_path: Path, monkeypatch):
    """A scratch directory must not turn the code gate red."""

    scratch = tmp_path / ".superpowers" / "sdd"
    scratch.mkdir(parents=True)
    (scratch / "dispatch.md").write_text("d?ner kurali\n", encoding="utf-8")
    monkeypatch.setattr(architecture_check, "ROOT", tmp_path)

    assert architecture_check.check_document_encoding() == []


def test_check_licenses_rejects_a_source_without_a_host(tmp_path: Path, monkeypatch):
    """Every license record must name the host it governs."""

    (tmp_path / "licenses.json").write_text(
        json.dumps(
            {
                "sources": [{"name": "known", "url": "https://known.example/reference"}],
                "packages": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(architecture_check, "ROOT", tmp_path)

    errors = architecture_check.check_licenses()

    assert errors == ["source host missing or invalid: known"]


def test_check_licenses_accepts_a_source_with_a_host(tmp_path: Path, monkeypatch):
    (tmp_path / "licenses.json").write_text(
        json.dumps(
            {
                "sources": [{"name": "known", "host": "known.example"}],
                "packages": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(architecture_check, "ROOT", tmp_path)

    assert architecture_check.check_licenses() == []
