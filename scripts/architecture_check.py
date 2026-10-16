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


def check_document_encoding() -> list[str]:
    """Reject replacement characters and lost Turkish letters in Markdown docs.

    A UTF-8 file can still contain literal ``?`` characters where an earlier
    ANSI conversion discarded a Turkish letter.  The word-boundary pattern is
    deliberately narrow so normal question punctuation remains valid.
    """

    errors: list[str] = []
    suspicious_word = re.compile(r"(?<=\w)\?(?=\w)")
    for path in ROOT.rglob("*.md"):
        if ".git" in path.parts:
            continue
        relative = path.relative_to(ROOT).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"non UTF-8 documentation: {relative}: {exc}")
            continue
        if "\ufffd" in content:
            errors.append(f"replacement character in documentation: {relative}")
        if suspicious_word.search(content):
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
            if not isinstance(item, dict) or item.get("kind") not in governed_kinds:
                continue
            name = str(item.get("name", "<unnamed>"))
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
        + check_document_encoding()
        + check_exception_logging()
        + check_secrets()
        + check_legacy_media_facades()
        + check_tls_policy()
        + check_licenses()
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
