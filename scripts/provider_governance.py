#!/usr/bin/env python3
"""Report source governance and optionally check manifest link health.

The default command is offline and inspects only the registered feature
manifest. ``--check-links`` is a separate, explicitly gated live operation;
it checks reachability without changing legal approval state or archiving full
URLs and response bodies.
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
from urllib.parse import urlsplit

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.features.provider_governance import (
    GovernanceApprovalError,
    build_approval_review_template,
    governance_report,
    load_approval_manifest,
)
from src.security.tls import httpx_verify_kwargs

# The host-side deploy preflight intentionally runs before the production
# image exists.  Some VDS images have a system Python with incomplete optional
# database bindings (for example a PyOpenSSL/PyMongo mismatch), so importing
# ChatEngine there can fail even though the Docker runtime is healthy.  Keep
# the full runtime registry when available and fall back to the checked-in
# review worksheet for the offline governance decision.
try:  # pragma: no cover - the fallback is exercised in the host image
    from config import Config
    from src.chat_engine import ChatEngine
    from src.key_manager import KeyManager
    from src.mcp_manager import McpManager
except Exception:  # noqa: BLE001 - fail closed through the static path below
    Config = None  # type: ignore[assignment,misc]
    ChatEngine = None  # type: ignore[assignment,misc]
    KeyManager = None  # type: ignore[assignment,misc]
    McpManager = None  # type: ignore[assignment,misc]


def _static_registry():
    """Build checked-in source metadata without optional runtime services."""

    from src.features.catalog import register_runtime_local_features
    from src.features.contracts import FeatureDefinition
    from src.features.registry import FeatureRegistry

    worksheet = PROJECT_ROOT / "deploy" / "evidence" / "provider-approval-review-template.json"
    payload = json.loads(worksheet.read_text(encoding="utf-8"))
    rows = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise GovernanceProbeError("provider_review_template_invalid")
    registry = FeatureRegistry()
    # The worksheet predates several game features.  A host with incomplete
    # optional Python dependencies still has to review every catalog source;
    # otherwise production preflight can silently pass an unreviewed game.
    register_runtime_local_features(registry)
    catalog_keys = {definition.key for definition in registry.definitions()}
    for row in rows:
        if not isinstance(row, dict):
            raise GovernanceProbeError("provider_review_row_invalid")
        key = str(row.get("feature_key") or "").strip()
        if not key:
            raise GovernanceProbeError("provider_review_feature_invalid")
        if key in catalog_keys:
            continue
        registry.register_feature(
            FeatureDefinition(
                key=key,
                category="external",
                input_model=str(row.get("provider_key") or key),
                output_type="feature_result",
                provider_key=row.get("provider_key"),
                cost_class="network",
                audit_policy="provider_call",
                source_name=str(row.get("source_name") or key),
                source_license="provider review required",
                source_governance=str(row.get("current_source_governance") or "pending"),
                robots_policy=str(row.get("current_robots_policy") or "review_required"),
                terms_review=str(row.get("current_terms_review") or "review_required"),
                production_signoff=str(row.get("current_production_signoff") or "required"),
            ),
            None,
        )
    return registry


def _runtime_components():
    if ChatEngine is None or KeyManager is None or McpManager is None or Config is None:
        return None
    return ChatEngine(KeyManager(), McpManager(Config.MCP_SERVERS))


async def collect(
    *,
    require_approved: bool = False,
    approval_manifest: Path | None = None,
    disabled_features: set[str] | frozenset[str] | None = None,
) -> dict:
    engine = _runtime_components()
    if engine is None:
        registry = _static_registry()
        approvals = load_approval_manifest(approval_manifest) if approval_manifest else None
        return governance_report(
            registry,
            require_approved=require_approved,
            approvals=approvals,
            disabled_features=disabled_features,
        )
    try:
        approvals = load_approval_manifest(approval_manifest) if approval_manifest else None
        return governance_report(
            engine.feature_registry,
            require_approved=require_approved,
            approvals=approvals,
            disabled_features=disabled_features,
        )
    finally:
        await engine.close()


class GovernanceProbeError(ValueError):
    """Stable, operator-facing governance probe error."""


def build_approval_evidence(governance: dict[str, object]) -> dict[str, object]:
    """Convert a fully reviewed governance report to the live-audit schema.

    A source being technically marked ``approved`` in code is not enough for
    this artifact: every row must carry a bounded approval ID from the
    operator manifest.  That prevents a hand-written green report from being
    mistaken for legal/robots evidence.
    """

    rows = governance.get("features") if isinstance(governance, dict) else None
    source_rows = rows if isinstance(rows, list) else []
    features: list[dict[str, str]] = []
    for row in source_rows:
        if not isinstance(row, dict):
            continue
        feature_key = str(row.get("feature_key") or "")
        approval_id = str(row.get("approval_id") or "")
        if row.get("status") == "approved" and approval_id:
            features.append(
                {
                    "feature_key": feature_key[:128],
                    "status": "approved",
                    "evidence_id": approval_id[:160],
                }
            )
    pending = max(0, len(source_rows) - len(features))
    ok = bool(governance.get("ok")) and bool(source_rows) and pending == 0 and len(features) == len(source_rows)
    return {
        "scope": "provider-governance-approval",
        "ok": ok,
        "features": features,
        "approved_count": len(features),
        "pending_count": pending,
        "rejected_count": 0,
        "secret_free": True,
    }


def _safe_host(url: str) -> str | None:
    parsed = urlsplit(str(url or ""))
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    return host[:120] if host else None


def _link_targets(registry) -> list[dict[str, str]]:
    """Collect manifest links without returning URLs in evidence."""

    targets: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for definition in registry.definitions():
        for kind, raw_url in (("source", definition.source_url), ("terms", definition.source_terms_url)):
            url = str(raw_url or "").strip()
            if not url:
                continue
            key = (definition.key, kind, url)
            if key in seen:
                continue
            seen.add(key)
            targets.append({"feature_key": str(definition.key)[:80], "link_kind": kind, "url": url})
    return targets


async def _check_link(client: httpx.AsyncClient, target: dict[str, str]) -> dict[str, object]:
    """Check one manifest URL with bounded, URL-free output."""

    url = target["url"]
    parsed = urlsplit(url)
    result: dict[str, object] = {
        "feature_key": target["feature_key"],
        "link_kind": target["link_kind"],
        "source_host": _safe_host(url),
        "status_code": None,
        "outcome": "invalid_url",
        "latency_ms": None,
    }
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return result
    started = time.perf_counter()
    try:
        response = await client.head(url, follow_redirects=False)
        # Some public pages reject HEAD, or return a redirect for HEAD while
        # serving the same URL directly for GET.  Probe the representation
        # once with a tiny bounded range, still without following redirects
        # and without retaining the response body.
        if response.status_code in {405, 501} or 300 <= response.status_code < 400:
            response = await client.get(
                url,
                headers={"Range": "bytes=0-2047"},
                follow_redirects=False,
            )
        status_code = int(response.status_code)
        if 200 <= status_code < 300:
            outcome = "ok"
        elif 300 <= status_code < 400:
            outcome = "redirect"
        else:
            outcome = "http_error"
        result.update({"status_code": status_code, "outcome": outcome})
    except (httpx.HTTPError, OSError):
        result["outcome"] = "unreachable"
    finally:
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


async def check_manifest_links(registry, *, client: httpx.AsyncClient | None = None, concurrency: int = 4) -> dict[str, object]:
    """Run bounded link health checks; approval metadata is never changed."""

    targets = _link_targets(registry)
    semaphore = asyncio.Semaphore(max(1, min(10, int(concurrency))))
    owned = client is None
    if owned:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(6.0, connect=3.0),
            follow_redirects=False,
            headers={"User-Agent": "Arelbot-governance-check/1.0"},
            **httpx_verify_kwargs(),
        )

    async def run(target: dict[str, str]) -> dict[str, object]:
        async with semaphore:
            return await _check_link(client, target)

    try:
        results = await asyncio.gather(*(run(target) for target in targets))
    finally:
        if owned and client is not None:
            await client.aclose()
    passed = sum(1 for result in results if result["outcome"] == "ok")
    return {
        "ok": len(results) == passed,
        "scope": "live-link-health",
        "link_count": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "approval_unchanged": True,
        "results": results,
    }


def _write_report(path: Path, payload: dict[str, object]) -> None:
    """Write evidence atomically and reject symlink targets/parents."""

    if path.exists() and path.is_symlink():
        raise GovernanceProbeError("report_target_symlink")
    for parent in (path.parent, *path.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise GovernanceProbeError("report_parent_symlink")
    parent = path.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        temporary.replace(path)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-approved",
        action="store_true",
        help="fail unless every external source has explicit production approval",
    )
    parser.add_argument(
        "--approval-manifest",
        type=Path,
        help="load operator-reviewed, fingerprint-bound approval evidence",
    )
    parser.add_argument(
        "--approval-evidence",
        action="store_true",
        help="emit the stable provider-governance-approval evidence schema",
    )
    parser.add_argument(
        "--review-template",
        action="store_true",
        help="emit a safe fingerprint-bound review worksheet (never an approval)",
    )
    parser.add_argument(
        "--disabled-features",
        default="",
        help="comma-separated feature keys explicitly disabled for this deployment",
    )
    parser.add_argument(
        "--check-links",
        action="store_true",
        help="perform bounded live checks for manifest source and terms links",
    )
    parser.add_argument("--live", action="store_true", help="allow the explicit live link check")
    parser.add_argument("--confirm-live", action="store_true", help="second live link-check confirmation")
    parser.add_argument("--concurrency", type=int, default=4, help="maximum concurrent link checks (1-10)")
    parser.add_argument("--report", type=Path, help="write a safe governance report atomically")
    args = parser.parse_args()

    if args.check_links and not (
        args.live
        and args.confirm_live
        and os.getenv("ARELBOT_GOVERNANCE_LINKS_LIVE") == "1"
    ):
        result = {"ok": False, "error": "live_link_gate_required"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    if args.approval_evidence and (not args.require_approved or args.check_links):
        result = {"ok": False, "error": "approval_evidence_requires_require_approved"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    if args.review_template and (args.approval_evidence or args.check_links or args.require_approved):
        result = {"ok": False, "error": "review_template_flags_conflict"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    if not 1 <= int(args.concurrency) <= 10:
        result = {"ok": False, "error": "link_concurrency_invalid"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    async def run() -> dict[str, object]:
        engine = _runtime_components()
        if engine is None:
            registry = _static_registry()
            disabled_features = {
                item.strip().casefold()
                for item in str(args.disabled_features or "").split(",")
                if item.strip()
            }
            try:
                approvals = load_approval_manifest(args.approval_manifest) if args.approval_manifest else None
            except GovernanceApprovalError as exc:
                return {"ok": False, "error": exc.code}
            if args.approval_evidence and approvals is None:
                return {"ok": False, "error": "approval_manifest_required"}
            if args.check_links:
                # A static worksheet intentionally has no URLs.  Do not turn
                # that absence into a false green live-link report.
                return {"ok": False, "error": "governance_runtime_dependencies_unavailable"}
            governance = governance_report(
                registry,
                require_approved=args.require_approved,
                approvals=approvals,
                disabled_features=disabled_features,
            )
            if args.review_template:
                return build_approval_review_template(registry)
            return build_approval_evidence(governance) if args.approval_evidence else governance
        try:
            disabled_features = {
                item.strip().casefold()
                for item in str(args.disabled_features or "").split(",")
                if item.strip()
            }
            try:
                approvals = load_approval_manifest(args.approval_manifest) if args.approval_manifest else None
            except GovernanceApprovalError as exc:
                return {"ok": False, "error": exc.code}
            if args.approval_evidence and approvals is None:
                return {"ok": False, "error": "approval_manifest_required"}
            if args.review_template:
                return build_approval_review_template(engine.feature_registry)
            governance = governance_report(
                engine.feature_registry,
                require_approved=args.require_approved,
                approvals=approvals,
                disabled_features=disabled_features,
            )
            if not args.check_links:
                return build_approval_evidence(governance) if args.approval_evidence else governance
            links = await check_manifest_links(engine.feature_registry, concurrency=args.concurrency)
            return {
                "ok": bool(governance.get("ok")) and bool(links.get("ok")),
                "governance": governance,
                "links": links,
            }
        finally:
            await engine.close()

    # Keep provider/HTTP diagnostics out of the operator report, but restore
    # the process-wide logging threshold when ``main`` is called in-process by
    # tests or another maintenance command.  A CLI subprocess would otherwise
    # be harmless; an embedded call would silently mute every later log.
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        result = asyncio.run(run())
    finally:
        logging.disable(previous_disable)
    if args.report:
        try:
            _write_report(args.report, result)
        except GovernanceProbeError:
            result = {"ok": False, "error": "report_write_failed"}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
