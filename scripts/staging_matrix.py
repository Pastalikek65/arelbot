#!/usr/bin/env python3
"""Run an explicit, bounded external-provider staging matrix.

The normal release gate is deliberately offline.  This command is the
separate path for a staging environment that is allowed to contact the public
providers.  It never accepts an arbitrary URL or provider name: cases select
registered Arelbot features and pass only bounded scalar parameters through
the same ``FeatureExecutor`` used by Telegram and AI surfaces.

Usage (validation only; no network):

    python scripts/staging_matrix.py --config docs/staging-matrix.example.json

Usage (explicit live staging run):

    $env:ARELBOT_STAGING_LIVE = "1"
    python scripts/staging_matrix.py --config staging-matrix.json --live --confirm-live

The live switches are intentionally redundant.  A copied config must not
turn a CI job or a developer laptop into an accidental public-site scraper.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import ssl
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Config
from src.chat_engine import ChatEngine
from src.key_manager import KeyManager
from src.mcp_manager import McpManager
from src.security.log_redaction import summarize_exception


MAX_CASES = 64
MAX_PARAMS = 16
MAX_STRING = 120
MAX_CONCURRENCY = 10


def write_report(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write a bounded staging report without following symlinks."""
    target = Path(path)
    if target.exists() and target.is_symlink():
        raise MatrixConfigError("staging report target cannot be a symlink")
    raw_parent = target.parent
    for candidate in (raw_parent, *raw_parent.parents):
        if candidate.exists() and candidate.is_symlink():
            raise MatrixConfigError("staging report parent cannot be a symlink")
    parent = target.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        temporary.replace(target)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


@contextmanager
def _extra_ca_bundle():
    """Temporarily merge an explicitly configured extra CA without weakening TLS."""

    extra_path = os.getenv("ARELBOT_EXTRA_CA_CERT")
    if not extra_path:
        yield
        return
    certificate_path = Path(extra_path)
    if not certificate_path.is_file():
        raise MatrixConfigError("ARELBOT_EXTRA_CA_CERT yolu bulunamadı")
    raw = certificate_path.read_bytes()
    try:
        if raw.startswith(b"-----BEGIN CERTIFICATE-----"):
            extra_pem = raw
            # Ask OpenSSL to validate PEM framing and certificate data.
            ssl.create_default_context().load_verify_locations(cadata=extra_pem.decode("ascii"))
        else:
            from cryptography import x509
            from cryptography.hazmat.primitives import serialization

            cert = x509.load_der_x509_certificate(raw)
            extra_pem = cert.public_bytes(serialization.Encoding.PEM)
    except Exception as exc:
        raise MatrixConfigError("ARELBOT_EXTRA_CA_CERT geçerli PEM/DER sertifikası değil") from exc

    try:
        import certifi

        base_path = Path(os.getenv("SSL_CERT_FILE") or certifi.where())
    except ImportError as exc:
        raise MatrixConfigError("sertifika bundle tabanı bulunamadı") from exc
    if not base_path.is_file():
        raise MatrixConfigError("SSL_CERT_FILE bundle yolu bulunamadı")

    previous = {name: os.environ.get(name) for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")}
    with tempfile.NamedTemporaryFile(prefix="arelbot-staging-ca-", suffix=".pem", delete=False) as bundle:
        bundle.write(base_path.read_bytes())
        bundle.write(b"\n")
        bundle.write(extra_pem)
        bundle_path = Path(bundle.name)
    try:
        # httpx/httpcore use SSL_CERT_FILE when trust_env is enabled. Keep
        # REQUESTS_CA_BUNDLE aligned for any bounded adapter still using it.
        os.environ["SSL_CERT_FILE"] = str(bundle_path)
        os.environ["REQUESTS_CA_BUNDLE"] = str(bundle_path)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        bundle_path.unlink(missing_ok=True)


class MatrixConfigError(ValueError):
    """Raised when an operator supplied staging matrix is unsafe or invalid."""


@dataclass(frozen=True)
class MatrixCase:
    case_id: str
    feature: str
    params: dict[str, Any]
    expect_source: str | None = None
    repeat: bool = True


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    feature: str
    ok: bool
    first_ok: bool | None
    second_ok: bool | None
    first_cache_hit: bool | None
    second_cache_hit: bool | None
    stale: bool | None
    source: str | None
    source_host: str | None
    latency_ms: float
    second_latency_ms: float | None
    error_code: str | None = None
    detail: str | None = None


def _bounded_scalar(value: Any, *, field: str) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            raise MatrixConfigError(f"{field} çok uzun")
        if any(ord(char) < 32 for char in value):
            raise MatrixConfigError(f"{field} kontrol karakteri içeriyor")
        return value
    raise MatrixConfigError(f"{field} yalnızca basit değer taşıyabilir")


def load_cases(path: Path, *, feature_keys: set[str] | None = None) -> list[MatrixCase]:
    """Load and validate a matrix without importing or contacting providers."""

    try:
        # PowerShell's Windows UTF-8 writer may add a BOM; accept it while
        # keeping the file content strictly decoded as UTF-8.
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MatrixConfigError("staging matrix okunamadı") from exc
    raw_cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(raw_cases, list) or not raw_cases:
        raise MatrixConfigError("cases boş olamaz")
    if len(raw_cases) > MAX_CASES:
        raise MatrixConfigError(f"en fazla {MAX_CASES} case desteklenir")

    cases: list[MatrixCase] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_cases):
        if not isinstance(raw, dict):
            raise MatrixConfigError(f"case[{index}] nesne olmalı")
        case_id = str(raw.get("id") or "").strip()
        feature = str(raw.get("feature") or "").strip()
        params = raw.get("params") or {}
        if not case_id or len(case_id) > 80 or not case_id.replace("_", "").replace("-", "").isalnum():
            raise MatrixConfigError(f"case[{index}] geçersiz id")
        if case_id in seen:
            raise MatrixConfigError(f"tekrar eden case id: {case_id}")
        if feature_keys is not None and feature not in feature_keys:
            raise MatrixConfigError(f"kayıtlı olmayan feature: {feature}")
        if not isinstance(params, dict) or len(params) > MAX_PARAMS:
            raise MatrixConfigError(f"{case_id} params geçersiz")
        bounded = {str(key)[:64]: _bounded_scalar(value, field=f"{case_id}.{key}") for key, value in params.items()}
        expect_source = raw.get("expect_source")
        if expect_source is not None:
            expect_source = str(expect_source).strip()
            if not expect_source or len(expect_source) > 120:
                raise MatrixConfigError(f"{case_id} expect_source geçersiz")
        repeat = bool(raw.get("repeat", True))
        seen.add(case_id)
        cases.append(MatrixCase(case_id, feature, bounded, expect_source, repeat))
    return cases


def _safe_source_host(source_url: str | None) -> str | None:
    if not source_url:
        return None
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return parsed.hostname.lower()[:120]


def _host_matches_expected(source_url: str | None, expected: str | None) -> bool:
    if not expected or not source_url:
        return True
    host = _safe_source_host(source_url)
    expected_host = urlparse(expected).hostname or expected
    expected_host = expected_host.lower().strip().rstrip(".")
    return bool(host and (host == expected_host or host.endswith("." + expected_host)))


async def run_case(
    case: MatrixCase,
    executor: Any,
    *,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> CaseResult:
    """Execute one case through FeatureExecutor and optionally verify cache hit."""

    started = time.perf_counter()
    first = await executor.execute(case.feature, case.params)
    first_latency = (time.perf_counter() - started) * 1000
    second = None
    second_latency = None
    if case.repeat:
        # Yield once so stale-refresh tasks can be scheduled deterministically
        # in tests without adding an artificial network delay.
        await sleep(0)
        started = time.perf_counter()
        second = await executor.execute(case.feature, case.params)
        second_latency = (time.perf_counter() - started) * 1000

    source_url = (second or first).source_url
    source_host = _safe_source_host(source_url)
    source_ok = _host_matches_expected(source_url, case.expect_source)
    ok = bool(first.ok and (second.ok if second is not None else True) and source_ok)
    if case.repeat:
        # A repeat is a cache assertion only when the first response was
        # successful.  Providers with cache_ttl=0 are explicitly allowed to
        # report two successful upstream calls.
        definition = getattr(executor.registry.get_feature(case.feature), "definition", None)
        cacheable = bool(definition and definition.cache_ttl_seconds)
        if cacheable and first.ok and second is not None and not second.cache_hit:
            ok = False
    error_code = (second or first).error_code
    detail = None
    if not ok:
        # Keep the machine-readable report useful without copying provider
        # exception text, URLs, response bodies, or credentials into an
        # artifact that may be archived outside the bot host.
        detail = "staging_case_failed"
        if not source_ok:
            detail = "source_host_mismatch"
    return CaseResult(
        case_id=case.case_id,
        feature=case.feature,
        ok=ok,
        first_ok=first.ok,
        second_ok=second.ok if second is not None else None,
        first_cache_hit=first.cache_hit,
        second_cache_hit=second.cache_hit if second is not None else None,
        stale=(second or first).stale,
        source=(second or first).source,
        source_host=source_host,
        latency_ms=round(first_latency, 2),
        second_latency_ms=round(second_latency, 2) if second_latency is not None else None,
        error_code=error_code,
        detail=detail,
    )


async def run_matrix(cases: list[MatrixCase], engine: Any, *, concurrency: int) -> dict[str, Any]:
    if concurrency < 1 or concurrency > MAX_CONCURRENCY:
        raise MatrixConfigError(f"concurrency 1-{MAX_CONCURRENCY} arasında olmalı")
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case: MatrixCase) -> CaseResult:
        async with semaphore:
            try:
                return await run_case(case, engine.feature_executor)
            except Exception as exc:  # one provider must not hide other cases
                return CaseResult(
                    case_id=case.case_id,
                    feature=case.feature,
                    ok=False,
                    first_ok=None,
                    second_ok=None,
                    first_cache_hit=None,
                    second_cache_hit=None,
                    stale=None,
                    source=None,
                    source_host=None,
                    latency_ms=0.0,
                    second_latency_ms=None,
                    error_code="STAGING_EXCEPTION",
                    detail=summarize_exception(exc, label="staging_case"),
                )

    results = await asyncio.gather(*(one(case) for case in cases))
    latencies = sorted(item.latency_ms for item in results if item.latency_ms >= 0)
    second_latencies = sorted(item.second_latency_ms for item in results if item.second_latency_ms is not None)

    def percentile(values: list[float], fraction: float) -> float | None:
        if not values:
            return None
        return round(values[int((len(values) - 1) * fraction)], 2)

    return {
        "ok": all(item.ok for item in results),
        "case_count": len(results),
        "passed": sum(item.ok for item in results),
        "failed": sum(not item.ok for item in results),
        "latency_ms": {"p50": percentile(latencies, 0.50), "p95": percentile(latencies, 0.95)},
        "repeat_latency_ms": {"p50": percentile(second_latencies, 0.50), "p95": percentile(second_latencies, 0.95)},
        "results": [asdict(item) for item in results],
    }


async def _live_run_from_file(path: Path, concurrency: int) -> dict[str, Any]:
    """Build and close the engine in one event loop.

    HTTP clients and background stale-refresh tasks are loop-bound. Keeping
    construction, execution and shutdown together prevents a staging command
    from leaving an async client attached to a closed loop.
    """

    with _extra_ca_bundle():
        engine = ChatEngine(KeyManager(), McpManager(Config.MCP_SERVERS))
        try:
            cases = load_cases(path, feature_keys={item.key for item in engine.feature_registry.definitions()})
            return await run_matrix(cases, engine, concurrency=concurrency)
        finally:
            await engine.close()


def main() -> int:
    # This command's stdout is a machine-readable report. Engine/provider
    # informational logs go nowhere; operational failures are represented by
    # safe case codes in that report.
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="contact configured public providers")
    parser.add_argument("--confirm-live", action="store_true", help="second explicit live-run confirmation")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--report", type=Path, help="write a safe JSON report atomically")
    args = parser.parse_args()

    # Registry validation is still performed for dry-run, but importing the
    # engine is intentionally deferred until after the dangerous-mode check.
    if not args.live:
        try:
            cases = load_cases(args.config)
        except MatrixConfigError as exc:
            print(json.dumps({"ok": False, "mode": "validation-only", "error": str(exc)}, ensure_ascii=False))
            return 2
        payload = {"ok": True, "mode": "validation-only", "case_count": len(cases)}
        if args.report:
            write_report(args.report, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if not args.confirm_live or os.getenv("ARELBOT_STAGING_LIVE") != "1":
        if args.report:
            write_report(args.report, {"ok": False, "error": "live_gate_required"})
        print(json.dumps({"ok": False, "error": "live staging için --live, --confirm-live ve ARELBOT_STAGING_LIVE=1 gerekir"}, ensure_ascii=False))
        return 2

    # The engine is built once so all cases share the same provider runtime,
    # connection pools and request coalescing/cache behavior.
    try:
        report = asyncio.run(_live_run_from_file(args.config, args.concurrency))
    except MatrixConfigError as exc:
        print(json.dumps({"ok": False, "mode": "live-staging", "error": str(exc)}, ensure_ascii=False))
        return 2
    report["mode"] = "live-staging"
    if args.report:
        write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
