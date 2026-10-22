"""Static architecture guard used by CI and release checks."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import warnings
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HANDLER_DIR = ROOT / "src" / "handlers"
ORCHESTRATOR_PATH = ROOT / "src" / "orchestrator.py"
MAIN_PATH = ROOT / "main.py"
SOURCE_MOJIBAKE_PATTERN = re.compile(
    r"(?:\u00c3|\u00c4|\u00c5|\u00c2)(?:[\u0080-\u00bf]|\u0153|\u0178|\u017d)"
    r"|(?:\u00e2|\u00f0)[\u0080-\u00bf]{1,2}|\ufffd"
)
SECRET_PATTERN = re.compile(r"(?i)(telegram[_-]?token|api[_-]?key|secret|password)\s*=\s*['\"][^'\"]{8,}")
# Assignment names the shared ``SECRET_PATTERN`` misses.  It only recognises
# "telegram_token", "api_key" and the unprefixed "secret"/"password" endings, so
# a hard-coded encryption key, a Groq key list, a session key, a private key
# or any prefixed token slipped through it.  The name list is deliberately
# explicit: a generic "key" ending would match ordinary words.
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:encryption[_-]?key|groq[_-]?keys?|session[_-]?key|private[_-]?key"
    r"|[a-z0-9_]*[_-]?token|[a-z0-9_]*[_-]?password|[a-z0-9_]*[_-]?secret)"
    r"\s*=\s*['\"][^'\"]{8,}"
)
# Credential formats the redaction layer does not cover.  Each entry pairs a
# readable label with the pattern that recognises one provider key format.
# The leading boundary guard keeps ordinary words such as "risk-" or "task-"
# from being reported as an OpenAI key.
SECRET_VALUE_PATTERNS = (
    ("groq-key", re.compile(r"(?<![A-Za-z0-9])gsk_[A-Za-z0-9]{20,}")),
    ("gemini-key", re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{30,}")),
    ("tavily-key", re.compile(r"(?<![A-Za-z0-9])tvly-[A-Za-z0-9-]{20,}")),
    ("openai-key", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}")),
    ("cerebras-key", re.compile(r"(?<![A-Za-z0-9])csk-[A-Za-z0-9]{20,}")),
    ("nvidia-key", re.compile(r"(?<![A-Za-z0-9])nvapi-[A-Za-z0-9_-]{20,}")),
    ("connection-uri", re.compile(r"(?:mongodb|redis)(?:\+srv)?://[^:@\s\"']+:[^@\s\"']+@")),
)
# Source trees that the secret scan covers and the entrypoints it inspects.
SECRET_SCAN_DIRECTORIES = ("src", "scripts")
SECRET_SCAN_FILES = ("main.py", "config.py")
SECRET_SCAN_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        ".superpowers",
        ".venv",
        "__pycache__",
        "dist",
        "docs",
        "tests",
        "venv",
    }
)
SECRET_SCAN_EXCLUDED_NAMES = frozenset({".env", "licenses.json"})
# The guard file holds the credential patterns themselves, so it is excluded
# from its own scan exactly like ``check_secrets`` excludes it.
SECRET_SCAN_EXCLUDED_FILES = frozenset({"scripts/architecture_check.py"})
# Provider modules whose external hosts must be registered in licenses.json.
LICENSE_PROVIDER_DIRECTORIES = ("src/features/providers", "src/media/providers")
PROVIDER_HOST_PATTERN = re.compile(r"https://(?P<host>[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)")
REGISTRY_URL_HOST_PATTERN = re.compile(r"https?://(?P<host>[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)")
# Directories the repository ignores.  A scratch directory or a local agent
# tool directory must not turn a code gate red, so the document scan skips
# them.  Every other path in the working tree keeps being scanned.
DOCUMENT_SCAN_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        ".serena",
        ".superpowers",
        ".venv",
        "__pycache__",
        "dist",
        "downloads",
        "env",
        "scratch",
        "temp",
        "venv",
    }
)
# Public top-level domains.  A bare string in a provider module is only a
# host candidate when it ends with one of these real suffixes, so module
# names, CSS selectors and file names never become candidates.
PUBLIC_TOP_LEVEL_DOMAINS = frozenset(
    {
        "ae", "af", "al", "am", "ao", "app", "ar", "at", "au", "az", "ba", "bd",
        "be", "bf", "bg", "bh", "bi", "bj", "biz", "br", "by", "ca", "cf", "cg",
        "ch", "ci", "cl", "cm", "cn", "co", "com", "cz", "de", "dev", "dj", "dk",
        "dz", "ec", "edu", "eg", "er", "es", "et", "eu", "fi", "fm", "fr", "ga",
        "ge", "gh", "gm", "gn", "gov", "gr", "gt", "gw", "hk", "hu", "id", "ie",
        "il", "in", "info", "iq", "ir", "is", "it", "jo", "jp", "ke", "kg", "kh",
        "km", "kn", "kr", "kw", "kz", "la", "lb", "lk", "lt", "lu", "lv", "ly",
        "ma", "me", "mg", "mk", "ml", "mm", "mn", "mr", "mt", "mu", "mv", "mw",
        "mx", "my", "mz", "na", "ne", "net", "ni", "nl", "no", "np", "nz", "om",
        "org", "pa", "pe", "ph", "pk", "pl", "pt", "py", "qa", "ro", "rs", "ru",
        "rw", "sa", "sd", "se", "sg", "si", "sk", "sl", "sn", "so", "sr", "ss",
        "st", "sv", "td", "tg", "th", "tj", "tm", "tn", "to", "tr", "tv", "tw",
        "tz", "ua", "ug", "uk", "us", "uy", "uz", "ve", "vn", "za", "zm", "zw",
    }
)
# Collection literals whose direct string elements may name an external host.
HOST_COLLECTION_LITERALS = frozenset({"set", "frozenset", "list", "tuple"})
# Any URI scheme, not only HTTP: a connection string carries a query string
# too, and the lost-character pattern must not read it as a broken letter.
DOCUMENT_URI_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://\S+")
TLS_BYPASS_PATTERNS = (
    re.compile(r"\bnocheckcertificate\b", re.IGNORECASE),
    re.compile(r"\bverify\s*=\s*False\b", re.IGNORECASE),
    re.compile(r"\bssl_version\s*=\s*['\"]?SSLv3", re.IGNORECASE),
    re.compile(r"\bCERT_NONE\b"),
)
DOCUMENT_MOJIBAKE_PATTERN = re.compile(
    r"(?:\u00c3|\u00c4|\u00c5|\u00c2)(?:[\u0080-\u00bf]|\u0153|\u0178|\u017d)"
)


def check_handlers() -> list[str]:
    errors = []
    forbidden = (
        "import httpx", "import requests", "import aiohttp", "import motor", "import redis",
        "import yfinance", "import matplotlib", "from yfinance", "from matplotlib",
    )
    direct_provider = re.compile(
        r"get_tool\(\s*[\"'](?:get_|search_flights|generate_story)[^\"']*[\"']\s*\)\s*\.execute"
    )
    for path in HANDLER_DIR.glob("*.py"):
        content = path.read_text(encoding="utf-8")
        for item in forbidden:
            if item in content:
                errors.append(f"handler imports forbidden network/storage module: {path}: {item}")
        if direct_provider.search(content):
            errors.append(f"handler calls a provider tool directly: {path}")
        if "._db" in content:
            errors.append(f"handler accesses database internals directly: {path}")
        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            errors.append(f"handler cannot be parsed: {path}: {exc}")
        else:
            if any(isinstance(node, ast.Attribute) and node.attr == "db" for node in ast.walk(tree)):
                errors.append(f"handler database access must use HandlerDataPort: {path}")
    return errors


def check_main_bootstrap() -> list[str]:
    """Keep the process composition root explicit and request-scoped."""

    if not MAIN_PATH.exists():
        return ["main.py missing"]
    try:
        tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [f"main.py cannot be parsed: {exc}"]
    errors: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name) and node.value.func.id == "Orchestrator":
                errors.append("main.py must instantiate Orchestrator inside __main__")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "register_handlers":
            if len(node.args.args) < 2:
                errors.append("main.py register_handlers must receive an orchestrator")
    return errors


def _command_literals(node: ast.AST) -> set[str]:
    """Extract command names from strings and literal sequences."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value.lstrip("/").strip().casefold()} if node.value.strip() else set()
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values: set[str] = set()
        for item in node.elts:
            values.update(_command_literals(item))
        return values
    return set()


def _manifest_commands() -> set[str]:
    """Read public commands from static feature definitions without imports."""

    commands: set[str] = set()
    for path in (ROOT / "src" / "chat_engine.py", ROOT / "src" / "features" / "catalog.py"):
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FeatureDefinition":
                for keyword in node.keywords:
                    if keyword.arg == "commands":
                        commands.update(_command_literals(keyword.value))
            if path.name == "catalog.py" and isinstance(node, ast.Assign):
                is_manifest = any(isinstance(target, ast.Name) and target.id == "SPATULA_FEATURES" for target in node.targets)
                if not is_manifest or not isinstance(node.value, (ast.Tuple, ast.List)):
                    continue
                for row in node.value.elts:
                    if isinstance(row, (ast.Tuple, ast.List)) and len(row.elts) >= 3:
                        commands.update(_command_literals(row.elts[2]))
    return commands


def check_command_surface() -> list[str]:
    """Ensure every manifest command has a Telegram handler and BotCommand."""

    if not MAIN_PATH.exists():
        return ["main.py missing"]
    try:
        tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    handlers: set[str] = set()
    menu: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "CommandHandler" and node.args:
            handlers.update(_command_literals(node.args[0]))
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "commands" for target in node.targets):
            if isinstance(node.value, (ast.Tuple, ast.List)):
                for row in node.value.elts:
                    if isinstance(row, (ast.Tuple, ast.List)) and row.elts:
                        menu.update(_command_literals(row.elts[0]))
    manifest = _manifest_commands()
    errors = [f"feature command has no Telegram handler: /{command}" for command in sorted(manifest - handlers)]
    errors.extend(f"feature command missing from BotCommand menu: /{command}" for command in sorted(manifest - menu))
    return errors


def check_orchestrator_boundaries() -> list[str]:
    """Keep Telegram-facing orchestrator methods on the storage port.

    ``Orchestrator.__init__`` is the composition root and may inject the
    database into domain services. Runtime methods, however, are Telegram
    update surfaces and must use ``HandlerDataPort`` just like handlers do.
    """

    if not ORCHESTRATOR_PATH.exists():
        return [f"orchestrator file missing: {ORCHESTRATOR_PATH}"]
    try:
        tree = ast.parse(ORCHESTRATOR_PATH.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [f"orchestrator cannot be parsed: {ORCHESTRATOR_PATH}: {exc}"]
    errors: list[str] = []
    for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
        if class_node.name != "Orchestrator":
            continue
        for method in (node for node in class_node.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
            if method.name in {"__init__", "_init_handlers"}:
                continue
            if any(isinstance(node, ast.Attribute) and node.attr == "db" for node in ast.walk(method)):
                errors.append(
                    f"orchestrator database access must use HandlerDataPort: {ORCHESTRATOR_PATH}:{method.lineno}"
                )
    return errors


def check_feature_metadata() -> list[str]:
    """Ensure every static FeatureDefinition carries operational metadata."""
    required = {
        "key",
        "category",
        "provider_key",
        "cache_ttl_seconds",
        "freshness_sla_seconds",
        "timeout_ms",
        "audit_policy",
    }
    errors: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            errors.append(f"cannot parse feature module {path}: {exc}")
            continue
        for node in __import__("ast").walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name != "FeatureDefinition":
                continue
            present = {keyword.arg for keyword in node.keywords if keyword.arg}
            missing = sorted(required - present)
            if missing:
                errors.append(f"feature metadata missing in {path}: {', '.join(missing)}")
    return errors


def check_encoding() -> list[str]:
    errors = []
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"non UTF-8 source: {relative}: {exc}")
            continue
        if SOURCE_MOJIBAKE_PATTERN.search(content):
            errors.append(f"possible mojibake: {relative}")
    return errors


def check_line_endings(path: Path) -> bool:
    """Report whether a source file still uses CRLF or lone-CR endings.

    Repository sources are LF-only.  A file that mixes terminators makes
    every tool that splits on ``\\n`` see only part of the physical lines,
    and Git refuses to normalize a blob that already contains CRLF, so a
    checkout keeps reintroducing mixed terminators.  The check therefore
    reads raw bytes and looks for any carriage return.  Generated caches
    and release bundles are not repository sources and are skipped.
    """

    if "__pycache__" in path.parts or "dist" in path.parts:
        return False
    return b"\r" in path.read_bytes()


def check_source_line_endings() -> list[str]:
    """Reject repository sources that are not LF-only.

    The scan mirrors :func:`check_encoding` so both guards see the same
    files, and the findings join the same error collection so the
    ``--strict`` exit code covers them.
    """

    errors: list[str] = []
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if check_line_endings(path):
            errors.append(f"non LF line endings: {relative}")
    return errors


def check_secrets() -> list[str]:
    errors = []
    skip = {".env", ".env.example", "scripts/architecture_check.py"}
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if relative in skip:
            continue
        if SECRET_PATTERN.search(path.read_text(encoding="utf-8")):
            errors.append(f"possible hard-coded secret: {relative}")
    return errors


def _secret_scan_targets(repo_root: Path) -> list[Path]:
    """List every file the secret scan is allowed to read.

    The scan covers the application source, the operational scripts and the
    two entrypoints.  Test fixtures reproduce leaking credentials on purpose,
    so every ``tests`` directory is excluded, and generated or local
    material such as ``.env`` files and release bundles is skipped as well.
    """

    candidates: list[Path] = []
    for name in SECRET_SCAN_DIRECTORIES:
        directory = repo_root / name
        if directory.is_dir():
            candidates.extend(directory.rglob("*"))
    for name in SECRET_SCAN_FILES:
        candidate = repo_root / name
        if candidate.is_file():
            candidates.append(candidate)
    targets: list[Path] = []
    for path in candidates:
        if not path.is_file():
            continue
        parts = path.relative_to(repo_root).parts
        if any(part in SECRET_SCAN_EXCLUDED_DIRECTORIES for part in parts[:-1]):
            continue
        relative = path.relative_to(repo_root).as_posix()
        if relative in SECRET_SCAN_EXCLUDED_FILES:
            continue
        name = parts[-1]
        if name in SECRET_SCAN_EXCLUDED_NAMES or name.startswith(".env") or name.endswith(".example"):
            continue
        targets.append(path)
    return sorted(targets)


def _secret_label(line: str) -> str | None:
    """Return the label of the first credential pattern the line matches.

    The assignment family that ``check_secrets`` already reports repo-wide is
    not repeated here; only the names that guard misses are added, so a
    finding never appears twice in two different shapes.
    """

    for label, pattern in SECRET_VALUE_PATTERNS:
        if pattern.search(line):
            return label
    if SECRET_ASSIGNMENT_PATTERN.search(line) and not SECRET_PATTERN.search(line):
        return "credential-assignment"
    return None


def check_secret_patterns(repo_root: Path) -> list[str]:
    """Reject credential material that is committed to the source tree.

    A finding reports the file and the line only.  The secret value itself
    never reaches the output, because the report would then copy the
    credential into CI logs.
    """

    errors: list[str] = []
    for path in _secret_scan_targets(repo_root):
        relative = path.relative_to(repo_root).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(content.splitlines(), 1):
            label = _secret_label(line)
            if label:
                errors.append(f"{relative}:{number}: possible hard-coded secret ({label})")
    return errors


def check_document_encoding() -> list[str]:
    """Reject replacement characters and lost Turkish letters in Markdown docs.

    A UTF-8 file can still contain literal ``?`` characters where an earlier
    ANSI conversion discarded a Turkish letter.  The word-boundary pattern is
    deliberately narrow so normal question punctuation remains valid.  URIs
    are removed before the pattern runs, because a query string such as
    ``?authSource=admin`` would otherwise read as a lost letter.
    """

    errors: list[str] = []
    suspicious_word = re.compile(r"(?<=\w)\?(?=\w)")
    for path in ROOT.rglob("*.md"):
        relative_parts = path.relative_to(ROOT).parts
        if any(part in DOCUMENT_SCAN_EXCLUDED_DIRECTORIES for part in relative_parts[:-1]):
            continue
        relative = path.relative_to(ROOT).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"non UTF-8 documentation: {relative}: {exc}")
            continue
        if "\ufffd" in content:
            errors.append(f"replacement character in documentation: {relative}")
        if suspicious_word.search(DOCUMENT_URI_PATTERN.sub(" ", content)):
            errors.append(f"possible lost character in documentation: {relative}")
        if DOCUMENT_MOJIBAKE_PATTERN.search(content):
            errors.append(f"possible mojibake in documentation: {relative}")
    return errors


def check_exception_logging() -> list[str]:
    """Reject exception messages interpolated before the redaction filter.

    Passing an exception object as a structured logging argument is safe
    because ``RedactionFilter`` reduces it to its type. ``str(exc)`` and
    f-string interpolation happen before the filter and can therefore leak
    credentials, URLs, or private driver payloads. Application code must use
    ``summarize_exception`` instead.
    """

    exception_names = {
        "e",
        "exc",
        "error",
        "exception",
        "log_e",
        "clean_e",
        "pack_err",
        "last_exception",
    }
    errors: list[str] = []

    def contains_raw_exception(node: ast.AST) -> bool:
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                if child.func.id in {"str", "repr"} and child.args:
                    first = child.args[0]
                    if isinstance(first, ast.Name) and first.id in exception_names:
                        return True
            if isinstance(child, ast.FormattedValue) and isinstance(child.value, ast.Name):
                if child.value.id in exception_names:
                    return True
        return False

    paths = [* (ROOT / "src").rglob("*.py")]
    main_path = ROOT / "main.py"
    if main_path.is_file():
        paths.append(main_path)
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"debug", "info", "warning", "error", "exception", "critical"}:
                continue
            if contains_raw_exception(node):
                errors.append(f"raw exception interpolation in {relative}:{node.lineno}")
    return errors


def check_tls_policy() -> list[str]:
    """Reject certificate-validation bypasses in application and scripts."""

    errors: list[str] = []
    for path in (*ROOT.rglob("*.py"), *ROOT.rglob("*.sh")):
        relative = path.relative_to(ROOT).as_posix()
        # Test fixtures may model rejected configurations, but application
        # code and operational scripts must never ship a bypass.
        if relative.startswith("src/tests/"):
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for pattern in TLS_BYPASS_PATTERNS:
            if pattern.search(content):
                errors.append(f"TLS verification bypass pattern in {relative}: {pattern.pattern}")
    return errors


def check_legacy_media_facades() -> list[str]:
    """Keep historical media imports from reintroducing bypass providers.

    ``src.video_downloader`` and ``src.tiktok_downloader`` remain import
    compatible for third-party scripts, but the real application has one
    typed media provider boundary.  A direct HTTP client, extractor import or
    hard-coded third-party endpoint here would silently create a second
    policy path and bypass SSRF, limits, health scoring and cleanup.
    """

    errors: list[str] = []
    forbidden = (
        "import httpx",
        "import requests",
        "import aiohttp",
        "import yt_dlp",
        "import instaloader",
        "cobalt",
        "tikwm",
        "api.cobalt",
        "www.tikwm.com",
    )
    for relative in ("src/video_downloader.py", "src/tiktok_downloader.py"):
        path = ROOT / relative
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8").casefold()
        for marker in forbidden:
            if marker.casefold() in content:
                errors.append(f"legacy media facade bypasses typed provider boundary: {relative}: {marker}")
    return errors


def _collection_name_from_expression(value: ast.AST) -> str | None:
    """Return the collection name of a ``Database._db["<name>"]`` expression."""

    if isinstance(value, ast.IfExp):
        value = value.body
    if not isinstance(value, ast.Subscript):
        return None
    if not isinstance(value.value, ast.Attribute) or value.value.attr != "_db":
        return None
    if not isinstance(value.slice, ast.Constant) or not isinstance(value.slice.value, str):
        return None
    return value.slice.value


def _database_collection_names(repo_root: Path) -> set[str]:
    """Read the Mongo collection names the database facade publishes.

    ``src/database.py`` exposes every collection as a property that returns
    ``Database._db["<name>"]``.  Reading the names from that module keeps the
    service check aligned with the real schema instead of guessing from a word
    list.  The module is parsed and never imported: importing it would open a
    Motor client connection.
    """

    path = repo_root / "src" / "database.py"
    if not path.is_file():
        return set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
        if class_node.name != "Database":
            continue
        for item in class_node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(
                isinstance(decorator, ast.Name) and decorator.id == "property"
                for decorator in item.decorator_list
            ):
                continue
            for statement in item.body:
                if not isinstance(statement, ast.Return) or statement.value is None:
                    continue
                name = _collection_name_from_expression(statement.value)
                if name:
                    names.add(name)
    return names


def _is_self_attribute(node: ast.AST) -> bool:
    """Report whether the expression reads an attribute of ``self``."""

    return isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self"


def _raw_collection_usage(node: ast.AST, collections: set[str]) -> str | None:
    """Return the collection name when a node exposes a raw collection handle.

    Three shapes hand a service the collection object itself: an attribute
    read on an injected facade (``self.db.notes``), a subscript on it
    (``self.db["notes"]``) and a dynamic lookup (``getattr(self.db, "notes")``).
    Calling a port method on the facade (``self.db.get_leaderboard(...)``) is
    not one of them, because a port method name is never a collection name.
    """

    if isinstance(node, ast.Attribute):
        if node.attr in collections and _is_self_attribute(node.value):
            return node.attr
        return None
    if isinstance(node, ast.Subscript):
        key = node.slice
        if (
            isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and key.value in collections
            and _is_self_attribute(node.value)
        ):
            return key.value
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
        if len(node.args) < 2:
            return None
        target, name = node.args[0], node.args[1]
        if (
            isinstance(name, ast.Constant)
            and isinstance(name.value, str)
            and name.value in collections
            and _is_self_attribute(target)
        ):
            return name.value
    return None


# Wired into main() in Task 4.1 (the service-layer violations are fixed in the same change).
def check_service_persistence(repo_root: Path) -> list[str]:
    """Reject services that build a raw Mongo collection.

    A service reads and writes through the storage layer.  The check takes the
    collection names from the database facade itself and reports every service
    that hands one of them to its own code, so a port method call such as
    ``self.db.get_leaderboard(...)`` stays allowed while a collection handle
    does not.
    """

    errors: list[str] = []
    services_dir = repo_root / "src" / "services"
    if not services_dir.is_dir():
        return errors
    if not (repo_root / "src" / "database.py").is_file():
        return ["src/database.py is missing: the Mongo collection names cannot be read"]
    collections = _database_collection_names(repo_root)
    if not collections:
        return ["src/database.py publishes no Mongo collection names"]
    for path in sorted(services_dir.rglob("*.py")):
        relative = path.relative_to(repo_root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            errors.append(f"{relative}: service module cannot be parsed: {exc}")
            continue
        matches: list[tuple[int, int]] = []
        for node in ast.walk(tree):
            if _raw_collection_usage(node, collections) is not None:
                matches.append((node.lineno, node.col_offset))
        reported: set[int] = set()
        for lineno, _ in sorted(matches):
            if lineno in reported:
                continue
            reported.add(lineno)
            errors.append(f"{relative}:{lineno}: service builds a raw Mongo collection")
    return errors


def check_licenses() -> list[str]:
    path = ROOT / "licenses.json"
    if not path.exists():
        return ["licenses.json is missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        errors = []
        if not isinstance(payload.get("sources"), list):
            errors.append("licenses.json.sources must be a list")
        if not isinstance(payload.get("packages"), list):
            errors.append("licenses.json.packages must be a list")
        governed_kinds = {
            "public-api",
            "public-web",
            "official-data",
            "official-public-page",
            "public-rss",
            "platform-api",
            "public-market-data",
        }
        allowed_governance = {"approved", "pending", "disabled", "not_required"}
        allowed_robots = {"allowed", "review_required", "disallowed", "not_applicable"}
        allowed_terms = {"approved", "review_required", "disallowed", "not_applicable"}
        allowed_signoff = {"approved", "required", "not_required", "rejected"}
        for item in payload.get("sources", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "<unnamed>"))
            host = item.get("host")
            if not isinstance(host, str) or not host.strip():
                errors.append(f"source host missing or invalid: {name}")
            if item.get("kind") not in governed_kinds:
                continue
            if item.get("source_governance") not in allowed_governance:
                errors.append(f"source governance missing or invalid: {name}")
            if item.get("robots_policy") not in allowed_robots:
                errors.append(f"robots policy missing or invalid: {name}")
            if item.get("terms_review") not in allowed_terms:
                errors.append(f"terms review missing or invalid: {name}")
            if item.get("production_signoff") not in allowed_signoff:
                errors.append(f"production signoff missing or invalid: {name}")
        licensed = {
            str(item.get("name", "")).lower().replace("_", "-")
            for item in payload.get("packages", [])
            if isinstance(item, dict)
        }
        constraints = ROOT / "constraints.txt"
        if constraints.exists():
            for line in constraints.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                package = re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].strip().lower().replace("_", "-")
                if package and package not in licensed:
                    errors.append(f"dependency has no license entry: {package}")
        return errors
    except json.JSONDecodeError as exc:
        return [f"licenses.json is invalid: {exc}"]
        return []


def _is_hostname(value: str) -> bool:
    """Report whether a captured URL authority is a plausible hostname."""

    if "." not in value:
        return False
    for label in value.split("."):
        if not label or label.startswith("-") or label.endswith("-"):
            return False
        if not all(character.isalnum() or character == "-" for character in label):
            return False
    return True


def _normalized_host(host: str) -> str:
    """Normalise a hostname so a registry entry covers its subdomains.

    A scheme, a path and an explicit port are dropped, and a leading
    ``www.`` is removed, so that ``https://www.example.com/path`` and
    ``example.com`` describe the same registrable host.
    """

    value = host.strip().casefold()
    for scheme in ("https://", "http://"):
        if value.startswith(scheme):
            value = value[len(scheme) :]
            break
    value = value.split("/", 1)[0].split(":", 1)[0]
    return value[4:] if value.startswith("www.") else value


def _collection_literal_strings(node: ast.AST) -> list[str]:
    """Return the direct string elements of a set, list or tuple literal.

    Only the literal's own elements are read.  A dictionary is deliberately
    excluded: a mapping key is a lookup alias rather than a host, and a
    mapping value is data the provider sends upstream.
    """

    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        elements = node.elts
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in HOST_COLLECTION_LITERALS
        and len(node.args) == 1
        and not node.keywords
        and isinstance(node.args[0], (ast.Set, ast.List, ast.Tuple))
    ):
        elements = node.args[0].elts
    else:
        return []
    return [
        element.value
        for element in elements
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]


def _public_host_candidate(value: str) -> str | None:
    """Return the normalized host when a bare string names a public host.

    The last label must be a real public top-level domain, so a module name,
    a CSS selector or a file name never reads as a host.
    """

    candidate = _normalized_host(value)
    if not _is_hostname(candidate):
        return None
    labels = candidate.split(".")
    if len(labels) < 2 or labels[-1] not in PUBLIC_TOP_LEVEL_DOMAINS:
        return None
    return candidate


def _bare_host_candidates(tree: ast.AST) -> list[tuple[int, str]]:
    """Return the bare host allowlist entries a provider module declares.

    A provider names an external source in two shapes: a literal URL and a
    bare host allowlist.  The literal scan reads the first shape; this pass
    reads the second one, so a host that never appears in a URL is still
    covered by the license registry.
    """

    candidates: list[tuple[int, str]] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        for value in _collection_literal_strings(node):
            host = _public_host_candidate(value)
            if host is None or host in seen:
                continue
            seen.add(host)
            candidates.append((getattr(node, "lineno", 0), host))
    return candidates



def _host_is_registered(host: str, registered: set[str]) -> bool:
    """Report whether a registry entry covers the used hostname."""

    candidate = _normalized_host(host)
    for entry in registered:
        if candidate == entry or candidate.endswith("." + entry):
            return True
    return False


def _provider_modules(repo_root: Path) -> list[Path]:
    modules: list[Path] = []
    for relative in LICENSE_PROVIDER_DIRECTORIES:
        directory = repo_root / relative
        if directory.is_dir():
            modules.extend(directory.rglob("*.py"))
    return sorted(modules)


def _registry_host(entry: dict) -> str | None:
    """Return the host a license record registers.

    The explicit ``host`` field wins.  A record that only carries a ``url``
    still names its source, so the host is derived from that URL; this keeps
    the registry usable before every record gained an explicit host.
    """

    host = entry.get("host")
    if host:
        return str(host)
    url = entry.get("url")
    if not url:
        return None
    match = REGISTRY_URL_HOST_PATTERN.search(str(url))
    return match.group("host") if match else None


def check_license_registry(repo_root: Path) -> list[str]:
    """Require every provider host to be registered in ``licenses.json``.

    A provider module may only reach external hosts that the license
    registry records.  A registry entry covers the hostname itself and every
    subdomain of it, so one entry can govern an API endpoint.  Hosts are read
    in both shapes a provider uses: a literal URL and a bare host allowlist.
    """

    registry_path = repo_root / "licenses.json"
    if not registry_path.is_file():
        return ["licenses.json is missing"]
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"licenses.json is invalid: {exc}"]
    if not isinstance(payload, dict):
        return ["licenses.json must be an object"]
    sources = payload.get("sources")
    if not isinstance(sources, list):
        return ["licenses.json.sources must be a list"]
    registered = {
        _normalized_host(host)
        for host in (_registry_host(item) for item in sources if isinstance(item, dict))
        if host
    }
    errors: list[str] = []
    for path in _provider_modules(repo_root):
        relative = path.relative_to(repo_root).as_posix()
        content = path.read_text(encoding="utf-8")
        reported: set[str] = set()
        for number, line in enumerate(content.splitlines(), 1):
            for match in PROVIDER_HOST_PATTERN.finditer(line):
                host = match.group("host")
                normalized = _normalized_host(host)
                if not _is_hostname(normalized) or normalized in reported:
                    continue
                if _host_is_registered(normalized, registered):
                    continue
                reported.add(normalized)
                errors.append(
                    f"{relative}:{number}: provider host is not registered in licenses.json ({host})"
                )
        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            errors.append(f"{relative}: provider module cannot be parsed: {exc}")
            continue
        for number, host in _bare_host_candidates(tree):
            if host in reported:
                continue
            if _host_is_registered(host, registered):
                continue
            reported.add(host)
            errors.append(
                f"{relative}:{number}: provider host is not registered in licenses.json ({host})"
            )
    return errors


def check_city_snapshot() -> list[str]:
    path = ROOT / "src" / "data" / "turkish_cities.json"
    if not path.exists():
        return ["city snapshot is missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cities = payload.get("cities", [])
        expected = payload.get("checksum_sha256")
        canonical = json.dumps(
            sorted(cities, key=lambda item: int(item.get("plate", 0))),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        errors = []
        if len(cities) != 81:
            errors.append(f"city snapshot must contain 81 provinces, found {len(cities)}")
        if expected != actual:
            errors.append("city snapshot checksum mismatch")
        return errors
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return [f"city snapshot is invalid: {exc}"]


def check_operational_documents() -> list[str]:
    """Prevent an accidentally empty or incomplete release contract."""
    required = {
        "docs/RELEASE_READINESS.md": ("Evidence summary", "Release decision", "Required evidence"),
        "docs/ARCHITECTURE.md": ("DataProvider", "Worker"),
        "docs/DATA_SOURCES.md": ("Lisans", "Production sign-off"),
    }
    errors: list[str] = []
    for relative, sections in required.items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"required operational document is missing: {relative}")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"operational document is not UTF-8: {relative}: {exc}")
            continue
        if not content.strip():
            errors.append(f"operational document is empty: {relative}")
        for section in sections:
            if section not in content:
                errors.append(f"operational document section missing: {relative}: {section}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="return non-zero for every violation")
    args = parser.parse_args()
    errors = (
        check_handlers()
        + check_main_bootstrap()
        + check_command_surface()
        + check_orchestrator_boundaries()
        + check_feature_metadata()
        + check_encoding()
        + check_source_line_endings()
        + check_document_encoding()
        + check_exception_logging()
        + check_secrets()
        + check_secret_patterns(ROOT)
        + check_legacy_media_facades()
        + check_tls_policy()
        + check_licenses()
        + check_license_registry(ROOT)
        + check_city_snapshot()
        + check_operational_documents()
    )
    for error in errors:
        print(f"ERROR: {error}")
    if errors and args.strict:
        return 1
    print(f"Architecture check complete: {len(errors)} issue(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
