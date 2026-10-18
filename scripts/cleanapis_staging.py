#!/usr/bin/env python3
"""Run an explicit, secret-free Clean APIs model staging probe.

The default mode validates the command contract without contacting the provider.
A live probe requires ``--live``, ``--confirm-live`` and
``ARELBOT_CLEANAPIS_LIVE=1``. It sends one bounded, fixed prompt and archives
only provider/model identity, latency and response-size metadata; keys and
model output are never printed or written to evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.production_preflight import read_dotenv
from src.security.error_codes import normalize_error_code


class CleanApisProbeError(ValueError):
    """Stable, non-sensitive probe failure code."""


def validate_live_gate(*, live: bool, confirm_live: bool, env_value: str | None) -> None:
    if not (live and confirm_live and str(env_value or "").strip() == "1"):
        raise CleanApisProbeError("live_gate_required")


def build_success_report(response: Any, *, latency_ms: float) -> dict[str, Any]:
    content = str(getattr(response, "content", "") or "")
    usage = getattr(response, "usage", {})
    return {
        "scope": "cleanapis-staging",
        "ok": True,
        "provider": str(getattr(response, "provider", "cleanapis"))[:64],
        "model": str(getattr(response, "model", ""))[:128],
        "latency_ms": round(max(0.0, float(latency_ms)), 2),
        "response_chars": min(len(content), 256_000),
        "usage_present": isinstance(usage, dict) and bool(usage),
        "secret_free": True,
    }


def build_failure_report(error: BaseException | object) -> dict[str, Any]:
    code = normalize_error_code(getattr(error, "code", ""), "PROVIDER_CALL_FAILED")
    if code == "INTERNAL_ERROR" or not getattr(error, "code", None):
        code = "PROVIDER_CALL_FAILED"
    return {
        "scope": "cleanapis-staging",
        "ok": False,
        "error": code,
        "exception_type": type(error).__name__[:128],
        "secret_free": True,
    }


def write_report(path: Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    if target.exists() and target.is_symlink():
        raise CleanApisProbeError("report_target_symlink")
    for parent in (target.parent, *target.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise CleanApisProbeError("report_parent_symlink")
    parent = target.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=True, indent=2)
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


def _load_probe_environment(env_file: Path) -> None:
    values = read_dotenv(env_file)
    for key in ("CLEANAPIS_URL", "CLEANAPIS_KEY", "CLEANAPIS_MODEL"):
        value = values.get(key)
        if value is not None:
            os.environ[key] = value


async def _run_live(env_file: Path) -> dict[str, Any]:
    _load_probe_environment(env_file)
    from config import Config
    from src.ai.openai_compatible import OpenAICompatibleProvider
    from src.ai.provider_base import CompletionRequest
    from src.key_manager import KeyManager, ServiceType
    from src.security.tls import httpx_verify_kwargs
    import httpx

    if not Config.CLEANAPIS_KEY:
        raise CleanApisProbeError("cleanapis_key_missing")
    client = httpx.AsyncClient(**httpx_verify_kwargs())
    provider = OpenAICompatibleProvider(
        service=ServiceType.CLEANAPIS,
        base_url=Config.CLEANAPIS_URL,
        model=Config.CLEANAPIS_MODEL,
        key_manager=KeyManager(),
        http_client=client,
        timeout_seconds=20.0,
    )
    started = time.perf_counter()
    try:
        response = await provider.complete(
            CompletionRequest(
                messages=[{"role": "user", "content": "2+2? Reply with only the number."}],
                task="fast",
                max_tokens=8,
            )
        )
        return build_success_report(response, latency_ms=(time.perf_counter() - started) * 1000)
    finally:
        await client.aclose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    logging.disable(logging.CRITICAL)
    args = parse_args()
    try:
        if not args.live and not args.confirm_live:
            payload = {
                "scope": "cleanapis-staging",
                "ok": True,
                "mode": "validation-only",
                "checks": ["live_gate_not_entered", "secret_free_report_contract"],
                "secret_free": True,
            }
        else:
            validate_live_gate(
                live=args.live,
                confirm_live=args.confirm_live,
                env_value=os.getenv("ARELBOT_CLEANAPIS_LIVE"),
            )
            payload = asyncio.run(_run_live(args.env_file.resolve()))
            payload["mode"] = "live-completion"
    except CleanApisProbeError as exc:
        payload = build_failure_report(exc)
    except Exception as exc:
        payload = build_failure_report(exc)
    if args.report:
        try:
            write_report(args.report, payload)
        except CleanApisProbeError:
            payload = build_failure_report(CleanApisProbeError("report_write_failed"))
    print(json.dumps(payload, ensure_ascii=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
