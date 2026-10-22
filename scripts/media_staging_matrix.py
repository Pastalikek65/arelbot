#!/usr/bin/env python3
"""Run an explicit, bounded live media-provider staging matrix.

The default mode validates only the matrix shape and performs no network
request. Live probing requires ``--live``, ``--confirm-live`` and
``ARELBOT_MEDIA_STAGING_LIVE=1``. Downloads additionally require
``--download``. Reports contain platform/host/status/latency metadata only;
URLs, titles, cookies and provider exception text are never persisted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.media.providers.ytdlp_provider import MediaDownloadError, YtDlpProvider
from src.media.url_normalizer import InvalidMediaURL, normalize_media_url, platform_for_url


class MatrixError(ValueError):
    """Stable operator-facing matrix failure."""


_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,48}$")
_PLATFORMS = {"instagram", "tiktok", "youtube", "x", "facebook", "reddit"}
_FULL_LIVE_PLATFORMS = frozenset({"instagram", "tiktok", "youtube", "x"})
_WAIVER_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,_:/()'_-]{2,240}$")
_MAX_CASES = 16
_MAX_LIVE_JOBS = 100
_MAX_LIVE_CONCURRENCY = 10
_DEFAULT_CHAT_COUNT = 100
_MEDIA_COMPLETION_SLO_MS = 10_000.0


def validate_live_gate(*, live: bool, confirm_live: bool, env_value: str | None) -> None:
    if not (live and confirm_live and str(env_value or "").strip() == "1"):
        raise MatrixError("live_gate_required")


def validate_platform_matrix(
    cases: list[dict[str, Any]],
    *,
    required: bool,
    waived_platforms: frozenset[str] | set[str] = frozenset(),
) -> None:
    """Fail before external calls unless every non-waived platform is present."""

    if not required:
        return
    waived = {str(item).strip().casefold() for item in waived_platforms if str(item).strip()}
    if not waived <= _FULL_LIVE_PLATFORMS:
        raise MatrixError("live_platform_waiver_invalid")
    enabled = {str(item.get("platform")) for item in cases if item.get("enabled")}
    if not (_FULL_LIVE_PLATFORMS - waived) <= enabled:
        raise MatrixError("live_platform_matrix_incomplete")


def validate_platform_waiver(
    waived_platforms: list[str] | tuple[str, ...] | set[str],
    *,
    approver: str | None,
    reason: str | None,
) -> frozenset[str]:
    """Validate an explicit, bounded owner waiver before network calls."""

    waived = frozenset(str(item).strip().casefold() for item in waived_platforms if str(item).strip())
    if not waived or not waived <= _FULL_LIVE_PLATFORMS:
        raise MatrixError("live_platform_waiver_invalid")
    normalized_approver = str(approver or "").strip()
    normalized_reason = str(reason or "").strip()
    if not _WAIVER_TEXT_RE.fullmatch(normalized_approver):
        raise MatrixError("live_platform_waiver_approver_invalid")
    if not _WAIVER_TEXT_RE.fullmatch(normalized_reason):
        raise MatrixError("live_platform_waiver_reason_invalid")
    return waived


def _safe_host(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").strip().lower().rstrip(".")
    return host[:120] if host else None


def load_matrix(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise MatrixError("matrix_file_invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise MatrixError("matrix_file_invalid") from exc
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list) or not cases or len(cases) > _MAX_CASES:
        raise MatrixError("matrix_cases_invalid")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in cases:
        if not isinstance(item, dict):
            raise MatrixError("matrix_case_invalid")
        case_id = str(item.get("id") or "").strip().casefold()
        if not _ID_RE.fullmatch(case_id) or case_id in seen:
            raise MatrixError("matrix_case_id_invalid")
        seen.add(case_id)
        enabled = bool(item.get("enabled", True))
        raw_url = str(item.get("url") or "").strip()
        if not enabled:
            result.append({"id": case_id, "enabled": False})
            continue
        if not raw_url:
            raise MatrixError("matrix_url_missing")
        try:
            normalized = normalize_media_url(raw_url)
        except InvalidMediaURL as exc:
            raise MatrixError("matrix_url_invalid") from exc
        platform = str(item.get("platform") or platform_for_url(normalized)).strip().casefold()
        if platform not in _PLATFORMS or platform_for_url(normalized) != platform:
            raise MatrixError("matrix_platform_mismatch")
        result.append({"id": case_id, "enabled": True, "url": normalized, "platform": platform})
    if not any(item.get("enabled") for item in result):
        raise MatrixError("matrix_has_no_enabled_case")
    return result


def _safe_report(path: Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    if target.exists() and target.is_symlink():
        raise MatrixError("report_target_symlink")
    for parent in (target.parent, *target.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise MatrixError("report_parent_symlink")
    parent = target.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    temporary = parent / f".{target.name}.{os.urandom(8).hex()}.tmp"
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


async def _run_case(
    provider: YtDlpProvider,
    case: dict[str, Any],
    semaphore: asyncio.Semaphore,
    download: bool,
    *,
    run_index: int = 0,
    chat_count: int = _DEFAULT_CHAT_COUNT,
    execution_state: dict[str, int] | None = None,
) -> dict[str, Any]:
    async with semaphore:
        started = time.perf_counter()
        if execution_state is not None:
            execution_state["active"] = execution_state.get("active", 0) + 1
            execution_state["max_active"] = max(
                execution_state.get("max_active", 0), execution_state["active"]
            )
        job_id = f"staging-{case['id']}-{os.urandom(4).hex()}"
        result: dict[str, Any] = {
            "case_id": case["id"],
            "run_index": int(run_index),
            "platform": case["platform"],
            # A slot is deliberately reported instead of a Telegram id. This
            # lets operators verify chat isolation without archiving user
            # identifiers or full update payloads.
            "chat_slot": int(run_index % max(1, chat_count)),
            "source_host": _safe_host(case["url"]),
            "ok": False,
            "mode": "download" if download else "probe",
            "error_code": None,
            "duration_ms": None,
            "media_duration_seconds": None,
            "file_size_bytes": None,
        }
        artifact: dict[str, Any] | None = None
        try:
            metadata = await provider.probe(case["url"])
            result["media_duration_seconds"] = round(float(metadata.get("duration") or 0), 3)
            if download:
                artifact = await provider.download(case["url"], job_id)
                result["file_size_bytes"] = int(artifact.get("size") or 0)
                if result["file_size_bytes"] <= 0:
                    raise MediaDownloadError("empty_result", "empty")
            result["ok"] = True
        except MediaDownloadError as exc:
            result["error_code"] = str(getattr(exc, "code", "media_error"))[:64]
        except Exception:
            result["error_code"] = "probe_failed" if not download else "download_failed"
        finally:
            provider.cleanup(artifact, job_id, str(provider.download_dir))
            result["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
            if execution_state is not None:
                execution_state["active"] = max(0, execution_state.get("active", 1) - 1)
        return result


async def run_live(
    cases: list[dict[str, Any]],
    *,
    download: bool,
    concurrency: int,
    jobs: int | None = None,
    chat_count: int = _DEFAULT_CHAT_COUNT,
    waived_platforms: frozenset[str] | set[str] = frozenset(),
    waiver_approver: str | None = None,
    waiver_reason: str | None = None,
) -> dict[str, Any]:
    """Run a bounded external-provider load sample.

    ``jobs`` is intentionally separate from ``chat_count``. Ten concurrent
    provider jobs can be distributed over a larger active-chat population,
    while the report still proves the worker limit and scope mapping. The
    defaults preserve the original one-request-per-enabled-case matrix.
    """

    waived = frozenset(str(item).strip().casefold() for item in waived_platforms if str(item).strip())
    if waived:
        validate_platform_waiver(waived, approver=waiver_approver, reason=waiver_reason)
    if not 1 <= int(concurrency) <= _MAX_LIVE_CONCURRENCY:
        raise MatrixError("live_concurrency_invalid")
    if not 1 <= int(chat_count) <= _MAX_LIVE_JOBS:
        raise MatrixError("live_chat_count_invalid")
    enabled = [case for case in cases if case.get("enabled")]
    requested_jobs = len(enabled) if jobs is None else int(jobs)
    if not 1 <= requested_jobs <= _MAX_LIVE_JOBS:
        raise MatrixError("live_jobs_invalid")
    expanded = [enabled[index % len(enabled)] for index in range(requested_jobs)]
    with tempfile.TemporaryDirectory(prefix="arelbot-media-staging-") as directory:
        provider = YtDlpProvider(
            directory,
            max_bytes=45 * 1024 * 1024,
            max_duration=0,
            timeout=20,
            cookie_vault=object(),
            cookie_enabled=False,
        )
        semaphore = asyncio.Semaphore(min(_MAX_LIVE_CONCURRENCY, int(concurrency)))
        execution_state = {"active": 0, "max_active": 0}
        results = await asyncio.gather(
            *[
                _run_case(
                    provider,
                    case,
                    semaphore,
                    download,
                    run_index=index,
                    chat_count=chat_count,
                    execution_state=execution_state,
                )
                for index, case in enumerate(expanded)
            ]
        )
        await provider.wait_for_background_cleanup()
    latencies = sorted(float(item["duration_ms"]) for item in results if item["duration_ms"] is not None)
    # Nearest-rank p95 keeps small staging samples honest: with two enabled
    # cases the slower case is the p95, rather than silently reporting only
    # the faster first element.
    def percentile(rank: float) -> float | None:
        if not latencies:
            return None
        return latencies[min(len(latencies) - 1, max(0, math.ceil(len(latencies) * rank) - 1))]

    platform_summary: dict[str, dict[str, int]] = {}
    for item in results:
        platform = str(item.get("platform") or "unknown")
        bucket = platform_summary.setdefault(platform, {"jobs": 0, "passed": 0, "failed": 0})
        bucket["jobs"] += 1
        bucket["passed" if item.get("ok") else "failed"] += 1
    p95 = percentile(0.95)
    report = {
        "ok": all(bool(item["ok"]) for item in results),
        "scope": "live-media-matrix",
        "mode": "live-download" if download else "live-probe",
        "case_count": len(results),
        "requested_jobs": requested_jobs,
        "concurrency_limit": int(concurrency),
        "max_active_jobs": execution_state["max_active"],
        "chat_count": int(chat_count),
        "chat_scope_mode": "synthetic_slot_mapping_only",
        "unique_chat_slots": len({int(item["chat_slot"]) for item in results}),
        "latency_scope": "provider_probe_and_download_only",
        "passed": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "latency_ms": {"p50": percentile(0.50), "p95": p95, "p99": percentile(0.99)},
        "completion_slo": {
            "target_ms": _MEDIA_COMPLETION_SLO_MS,
            "applicable": bool(download),
            "p95_ms": p95,
            "met": (p95 is not None and p95 <= _MEDIA_COMPLETION_SLO_MS) if download else None,
        },
        "platforms": platform_summary,
        "results": results,
        "secret_free": True,
    }
    if waived:
        report["waived_platforms"] = sorted(waived)
        report["waiver"] = {
            "approved_by": str(waiver_approver).strip(),
            "reason": str(waiver_reason).strip(),
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("docs/media-live-matrix.example.json"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--jobs",
        type=int,
        help="bounded number of external jobs; defaults to one per enabled case",
    )
    parser.add_argument(
        "--chat-count",
        type=int,
        default=_DEFAULT_CHAT_COUNT,
        help="synthetic chat slots used to verify scope mapping (no Telegram calls)",
    )
    parser.add_argument(
        "--require-slo",
        action="store_true",
        help="fail the command when download p95 exceeds the 10-second completion target",
    )
    parser.add_argument(
        "--require-platform-matrix",
        action="store_true",
        help="require enabled live cases for the non-waived Instagram, TikTok, YouTube and X set",
    )
    parser.add_argument(
        "--waive-platform",
        action="append",
        default=[],
        help="explicit owner waiver for one unavailable platform; repeatable",
    )
    parser.add_argument("--waiver-approver", help="bounded owner identity for a platform waiver")
    parser.add_argument("--waiver-reason", help="bounded reason for a platform waiver")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        cases = load_matrix(args.config.resolve())
        waived_platforms = frozenset(str(item).strip().casefold() for item in args.waive_platform if str(item).strip())
        if waived_platforms:
            validate_platform_waiver(
                waived_platforms,
                approver=args.waiver_approver,
                reason=args.waiver_reason,
            )
        elif args.waiver_approver or args.waiver_reason:
            raise MatrixError("live_platform_waiver_missing_platform")
        validate_platform_matrix(
            cases,
            required=args.require_platform_matrix,
            waived_platforms=waived_platforms,
        )
        if args.live or args.confirm_live or args.download:
            validate_live_gate(
                live=args.live,
                confirm_live=args.confirm_live,
                env_value=os.getenv("ARELBOT_MEDIA_STAGING_LIVE"),
            )
            payload = asyncio.run(
                run_live(
                    cases,
                    download=args.download,
                    concurrency=args.concurrency,
                    jobs=args.jobs,
                    chat_count=args.chat_count,
                    waived_platforms=waived_platforms,
                    waiver_approver=args.waiver_approver,
                    waiver_reason=args.waiver_reason,
                )
            )
            if args.require_slo and payload.get("completion_slo", {}).get("met") is not True:
                payload["ok"] = False
                payload["error"] = "media_completion_slo_not_met"
        else:
            payload = {
                "ok": True,
                "scope": "media-matrix-validation",
                "mode": "validation-only",
                "case_count": sum(1 for item in cases if item.get("enabled")),
                "disabled_count": sum(1 for item in cases if not item.get("enabled")),
                "platforms": sorted({item["platform"] for item in cases if item.get("enabled")}),
                "secret_free": True,
            }
        if args.report:
            _safe_report(args.report, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if payload.get("ok") else 1
    except MatrixError as exc:
        payload = {"ok": False, "error": str(exc)}
        if args.report:
            try:
                _safe_report(args.report, payload)
            except MatrixError:
                payload = {"ok": False, "error": "report_write_failed"}
        print(json.dumps(payload, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
