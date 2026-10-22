#!/usr/bin/env python3
"""Run the deterministic media platform/provider replay matrix.

This command never contacts Instagram, TikTok, YouTube, X, Facebook or
Reddit. It replays normalized public URL shapes through the production
``ProviderRegistry`` and validates the same artifact/cleanup boundary used by
the worker. The report intentionally contains case ids and bounded metadata,
never URLs, local paths or provider exception text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.media.providers.base import MediaMetadata, MediaRequest, ProviderHealth, validate_media_artifact
from src.media.providers.registry import ProviderRegistry
from src.media.url_normalizer import InvalidMediaURL, normalize_media_url, platform_for_url


class MatrixError(ValueError):
    """Stable replay matrix validation error."""


_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,48}$")
_PLATFORMS = frozenset({"instagram", "tiktok", "youtube", "x", "facebook", "reddit"})
_MAX_CASES = 16
_SENSITIVE_KEY_RE = re.compile(r"(?i)(?:token|cookie|password|authorization|api[_-]?key|private[_-]?key|secret)")
_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")


def load_matrix(path: Path) -> list[dict[str, str]]:
    """Load and normalize a bounded, local-only replay matrix."""

    if not path.is_file() or path.is_symlink():
        raise MatrixError("matrix_file_invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MatrixError("matrix_file_invalid") from exc
    raw_cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(raw_cases, list) or not raw_cases or len(raw_cases) > _MAX_CASES:
        raise MatrixError("matrix_cases_invalid")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_cases:
        if not isinstance(item, dict):
            raise MatrixError("matrix_case_invalid")
        case_id = str(item.get("id") or "").strip().casefold()
        if not _ID_RE.fullmatch(case_id) or case_id in seen:
            raise MatrixError("matrix_case_id_invalid")
        seen.add(case_id)
        raw_url = str(item.get("url") or "").strip()
        try:
            raw_parts = urlsplit(raw_url)
        except ValueError as exc:
            raise MatrixError("matrix_fixture_port_invalid") from exc
        if raw_parts.username or raw_parts.password:
            raise MatrixError("matrix_fixture_url_unsafe")
        try:
            raw_port = raw_parts.port
        except ValueError as exc:
            raise MatrixError("matrix_fixture_port_invalid") from exc
        if raw_port not in {None, 80, 443}:
            raise MatrixError("matrix_fixture_port_invalid")
        try:
            normalized = normalize_media_url(raw_url)
        except InvalidMediaURL as exc:
            raise MatrixError("matrix_url_invalid") from exc
        platform = str(item.get("platform") or platform_for_url(normalized)).strip().casefold()
        if platform not in _PLATFORMS or platform_for_url(normalized) != platform:
            raise MatrixError("matrix_platform_mismatch")
        result.append({"id": case_id, "url": normalized, "platform": platform})
    validate_fixture_matrix(result)
    return result


def validate_fixture_matrix(cases: list[dict[str, str]]) -> None:
    """Validate the bounded, public-shape-only replay contract.

    Replay cases are identifiers and URL shapes, not credentials or live
    download instructions.  Keeping this check separate makes it reusable by
    CI and prevents a future fixture from accidentally carrying an auth
    component or an unsupported platform.
    """

    if not cases or len(cases) > _MAX_CASES:
        raise MatrixError("matrix_cases_invalid")
    seen: set[str] = set()
    for case in cases:
        case_id = str(case.get("id") or "")
        url = str(case.get("url") or "")
        platform = str(case.get("platform") or "").casefold()
        if not _ID_RE.fullmatch(case_id) or case_id in seen:
            raise MatrixError("matrix_case_id_invalid")
        if platform not in _PLATFORMS:
            raise MatrixError("matrix_platform_invalid")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise MatrixError("matrix_fixture_url_unsafe")
        try:
            if parsed.port not in {None, 80, 443}:
                raise MatrixError("matrix_fixture_port_invalid")
        except ValueError as exc:
            raise MatrixError("matrix_fixture_port_invalid") from exc
        if platform_for_url(url) != platform:
            raise MatrixError("matrix_platform_mismatch")
        seen.add(case_id)


class _ReplayProvider:
    def __init__(self, platform: str, root: Path) -> None:
        self.name = f"replay-{platform}"
        self.platforms = frozenset({platform})
        self.root = root

    async def probe(self, request: MediaRequest) -> MediaMetadata:
        return MediaMetadata(title="Replay fixture", platform=request.platform, duration=2.0, size=32)

    async def download(self, request: MediaRequest) -> dict[str, Any]:
        directory = self.root / request.job_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "media.mp4"
        path.write_bytes(b"arelbot-media-replay-fixture\n")
        return {
            "path": str(path),
            "title": "Replay fixture",
            "duration": 2.0,
            "size": path.stat().st_size,
            "extension": ".mp4",
            "platform": request.platform,
        }

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, healthy=True, latency_ms=0.1)


async def run_replay(cases: list[dict[str, str]]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="arelbot-media-replay-") as directory:
        root = Path(directory)
        registry = ProviderRegistry([_ReplayProvider(platform, root) for platform in sorted(_PLATFORMS)])
        rows: list[dict[str, Any]] = []
        for index, case in enumerate(cases):
            request = MediaRequest(
                url=case["url"],
                job_id=f"replay-{index}-{case['id']}",
                platform=case["platform"],
                timeout_seconds=2.0,
            )
            artifact: dict[str, Any] | None = None
            cleanup = False
            try:
                metadata = await registry.probe(request)
                artifact, provider_name = await registry.download(request)
                validate_media_artifact(artifact)
                artifact_path = Path(artifact["path"])
                if not artifact_path.is_file():
                    raise MatrixError("artifact_missing")
                cleanup_path = artifact_path.parent
                artifact_path.unlink(missing_ok=True)
                cleanup_path.rmdir()
                cleanup = not cleanup_path.exists()
                rows.append(
                    {
                        "case_id": case["id"],
                        "platform": case["platform"],
                        "provider": provider_name,
                        "metadata_duration_seconds": metadata.duration,
                        "artifact_extension": artifact["extension"],
                        "artifact_size_bytes": int(artifact["size"]),
                        "cleanup": cleanup,
                        "ok": cleanup,
                    }
                )
            except Exception as exc:
                if artifact and artifact.get("path"):
                    try:
                        Path(str(artifact["path"])).unlink(missing_ok=True)
                    except OSError:
                        pass
                rows.append(
                    {
                        "case_id": case["id"],
                        "platform": case["platform"],
                        "provider": None,
                        "metadata_duration_seconds": None,
                        "artifact_extension": None,
                        "artifact_size_bytes": None,
                        "cleanup": False,
                        "ok": False,
                        "error_code": type(exc).__name__[:64],
                    }
                )
    platform_summary: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = platform_summary.setdefault(row["platform"], {"cases": 0, "passed": 0, "failed": 0})
        bucket["cases"] += 1
        bucket["passed" if row["ok"] else "failed"] += 1
    return {
        "ok": bool(rows) and all(row["ok"] for row in rows),
        "mode": "deterministic-replay",
        "case_count": len(rows),
        "passed": sum(1 for row in rows if row["ok"]),
        "failed": sum(1 for row in rows if not row["ok"]),
        "platforms": platform_summary,
        "results": rows,
        "secret_free": True,
        "network_calls": 0,
    }


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise MatrixError("report_target_symlink")
    for parent in (path.parent, *path.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise MatrixError("report_parent_symlink")
    parent = path.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    if _contains_sensitive(payload):
        raise MatrixError("report_secret_material")
    temporary = parent / f".{path.name}.{os.urandom(8).hex()}.tmp"
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _contains_sensitive(value: Any, *, key: str = "") -> bool:
    """Reject credentials/URLs in replay evidence before it reaches disk."""

    if isinstance(value, dict):
        for child_key, child_value in value.items():
            lowered = str(child_key).casefold()
            if lowered == "secret_free" and isinstance(child_value, bool):
                continue
            if _SENSITIVE_KEY_RE.search(lowered) and child_value not in (None, False, "", 0):
                return True
            if _contains_sensitive(child_value, key=lowered):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_sensitive(item, key=key) for item in value)
    if isinstance(value, str):
        return bool(_TOKEN_RE.search(value) or "authorization:" in value.casefold() or "cookie=" in value.casefold())
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "docs" / "media-replay-matrix.json")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = asyncio.run(run_replay(load_matrix(args.config)))
        if args.report:
            _write_report(args.report, report)
    except MatrixError as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
