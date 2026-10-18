from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from src.features.contracts import FeatureDefinition
from src.features.provider_governance import (
    GovernanceApprovalError,
    approval_matches,
    build_approval_review_template,
    governance_report,
    governance_status,
    load_approval_manifest,
    source_fingerprint,
    validate_governance,
)
from src.features.catalog import SOURCE_METADATA
from src.features.registry import FeatureRegistry
from scripts.provider_governance import build_approval_evidence, main as governance_main


def _feature(**overrides):
    values = {
        "key": "fixture",
        "category": "haber",
        "provider_key": "fixture_provider",
        "cost_class": "network",
        "source_name": "Fixture source",
        "source_url": "https://fixture.invalid/source",
        "source_license": "Fixture terms",
        "source_terms_url": "https://fixture.invalid/terms",
        "source_governance": "pending",
        "robots_policy": "review_required",
        "terms_review": "review_required",
        "production_signoff": "required",
    }
    values.update(overrides)
    return FeatureDefinition(
        key=values["key"],
        category=values["category"],
        provider_key=values["provider_key"],
        cost_class=values["cost_class"],
        source_name=values["source_name"],
        source_url=values["source_url"],
        source_license=values["source_license"],
        source_terms_url=values["source_terms_url"],
        source_governance=values["source_governance"],
        robots_policy=values["robots_policy"],
        terms_review=values["terms_review"],
        production_signoff=values["production_signoff"],
        timeout_ms=1000,
        cache_ttl_seconds=30,
        freshness_sla_seconds=60,
        audit_policy="provider_call",
    )


def test_pending_external_source_is_explicit_and_safe_by_default():
    definition = _feature()

    assert governance_status(definition) == "pending"
    assert validate_governance(definition) == ()


def test_approved_external_source_requires_both_reviews_and_signoff():
    definition = _feature(
        source_governance="approved",
        robots_policy="allowed",
        terms_review="approved",
        production_signoff="approved",
    )

    assert governance_status(definition) == "approved"
    assert validate_governance(definition, require_approved=True) == ()


def test_approval_cannot_bypass_missing_terms_or_robots_review():
    definition = _feature(source_governance="approved")

    errors = validate_governance(definition, require_approved=True)

    assert "robots_review_missing" in errors
    assert "terms_review_missing" in errors
    assert "production_signoff_missing" in errors


def test_governance_report_omits_source_urls_and_marks_pending_features():
    registry = FeatureRegistry()
    registry.register_feature(_feature(), SimpleNamespace())

    report = governance_report(registry)

    assert report["ok"] is True
    assert report["pending_count"] == 1
    assert report["features"][0]["status"] == "pending"
    assert "source_url" not in report["features"][0]


def test_require_approved_report_fails_pending_feature_with_stable_code():
    registry = FeatureRegistry()
    registry.register_feature(_feature(), SimpleNamespace())

    report = governance_report(registry, require_approved=True)

    assert report["ok"] is False
    assert report["errors"] == ["fixture:source_governance_not_approved"]


def test_require_approved_allows_explicitly_disabled_pending_feature():
    registry = FeatureRegistry()
    registry.register_feature(_feature(), SimpleNamespace())

    report = governance_report(
        registry,
        require_approved=True,
        disabled_features={"FIXTURE"},
    )

    assert report["ok"] is True
    assert report["approved_count"] == 0
    assert report["pending_count"] == 0
    assert report["disabled_count"] == 1
    assert report["features"][0]["status"] == "disabled"
    assert report["features"][0]["disabled_by_config"] is True


def test_unknown_disabled_feature_fails_closed():
    registry = FeatureRegistry()
    registry.register_feature(_feature(), SimpleNamespace())

    report = governance_report(registry, require_approved=True, disabled_features={"typo"})

    assert report["ok"] is False
    assert report["errors"] == ["typo:disabled_feature_unknown", "fixture:source_governance_not_approved"]


def test_pharmacy_governance_uses_canonical_root_not_runtime_city_path():
    source_name, source_url, _license, terms_url = SOURCE_METADATA["pharmacy"]

    assert source_name == "N\u00f6bet\u00e7i Eczaneler"
    assert source_url == "https://www.nobetcieczaneler.org/"
    assert terms_url == source_url


def _approval_payload(definition, *, now: datetime | None = None, **overrides):
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    payload = {
        "feature_key": definition.key,
        "approval_id": "review-2026-001",
        "source_fingerprint": source_fingerprint(definition),
        "reviewer": "platform-governance",
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
        "source_governance": "approved",
        "robots_policy": "allowed",
        "terms_review": "approved",
        "production_signoff": "approved",
    }
    payload.update(overrides)
    return payload


def _write_manifest(path: Path, payload: dict) -> None:
    path.write_text(json.dumps({"schema_version": 1, "approvals": [payload]}), encoding="utf-8")
    # Production requires a private evidence file on POSIX.  Make the
    # fixture portable so the same test has identical semantics in Docker.
    path.chmod(0o600)


def test_fingerprint_bound_approval_can_promote_pending_feature(tmp_path: Path):
    definition = _feature()
    path = tmp_path / "provider-approvals.json"
    _write_manifest(path, _approval_payload(definition))

    approvals = load_approval_manifest(path)
    approval = approvals[definition.key]

    assert approval_matches(definition, approval)
    assert governance_status(definition, approval) == "approved"
    registry = FeatureRegistry(require_provider_approval=True, provider_approvals=approvals)
    registry.register_feature(definition, SimpleNamespace())
    assert registry.feature_enabled(definition.key) is True
    status = registry.feature_status(definition.key)
    assert status["provider_approval_id"] == "review-2026-001"
    report = governance_report(registry, approvals=approvals, require_approved=True)
    assert report["approved_count"] == 1
    assert report["pending_count"] == 0
    assert report["errors"] == []
    assert report["features"][0]["robots_policy"] == "allowed"


def test_registry_governance_report_keeps_runtime_approval_snapshot(tmp_path: Path):
    definition = _feature()
    path = tmp_path / "provider-approvals.json"
    _write_manifest(path, _approval_payload(definition))
    approvals = load_approval_manifest(path)
    registry = FeatureRegistry(require_provider_approval=True, provider_approvals=approvals)
    registry.register_feature(definition, SimpleNamespace())

    report = registry.governance_report()

    assert report["ok"] is True
    assert report["approved_count"] == 1
    assert report["pending_count"] == 0
    assert report["features"][0]["approval_id"] == "review-2026-001"


def test_approval_fingerprint_change_fails_closed(tmp_path: Path):
    definition = _feature()
    path = tmp_path / "provider-approvals.json"
    payload = _approval_payload(definition, source_fingerprint="0" * 64)
    _write_manifest(path, payload)
    approval = load_approval_manifest(path)[definition.key]
    assert approval_matches(definition, approval) is False


def test_expired_or_incomplete_approval_is_rejected(tmp_path: Path):
    definition = _feature()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    path = tmp_path / "provider-approvals.json"
    payload = _approval_payload(
        definition,
        now=now - timedelta(days=3),
        expires_at=(now - timedelta(days=1)).isoformat(),
    )
    _write_manifest(path, payload)
    with pytest.raises(GovernanceApprovalError, match="approval_expired"):
        load_approval_manifest(path, now=now)

    payload = _approval_payload(definition, terms_review="review_required")
    _write_manifest(path, payload)
    with pytest.raises(GovernanceApprovalError, match="approval_status_incomplete"):
        load_approval_manifest(path)


def test_approval_evidence_requires_every_external_row_to_have_an_id():
    valid = build_approval_evidence(
        {
            "ok": True,
            "features": [
                {"feature_key": "weather", "status": "approved", "approval_id": "review-1"},
            ],
        }
    )
    assert valid["scope"] == "provider-governance-approval"
    assert valid["ok"] is True
    assert valid["approved_count"] == 1
    assert valid["pending_count"] == 0
    assert valid["secret_free"] is True

    incomplete = build_approval_evidence(
        {
            "ok": True,
            "features": [
                {"feature_key": "weather", "status": "approved", "approval_id": "review-1"},
                {"feature_key": "news", "status": "pending"},
            ],
        }
    )
    assert incomplete["ok"] is False
    assert incomplete["pending_count"] == 1
    assert incomplete["secret_free"] is True


def test_approval_review_template_is_safe_stable_and_not_loadable_as_approval():
    registry = FeatureRegistry()
    registry.register_feature(_feature(key="weather"), SimpleNamespace())
    registry.register_feature(
        _feature(
            key="currency",
            provider_key="currency_provider",
            source_name="Currency fixture",
        ),
        SimpleNamespace(),
    )

    template = build_approval_review_template(registry)

    assert template["schema_version"] == 1
    assert template["kind"] == "provider-approval-review-template"
    assert template["ok"] is True
    rows = template["features"]
    assert [row["feature_key"] for row in rows] == ["currency", "weather"]
    assert all(row["source_fingerprint"] for row in rows)
    assert all("source_url" not in row and "source_terms_url" not in row for row in rows)
    assert all(row["review_required"] is True for row in rows)
    currency_definition = next(item for item in registry.definitions() if item.key == "currency")
    assert rows[0]["source_fingerprint"] == source_fingerprint(currency_definition)
    assert "approvals" not in template


def test_review_template_cli_is_a_successful_non_approval_report(monkeypatch, capsys):
    previous_disable = logging.root.manager.disable
    monkeypatch.setattr(sys, "argv", ["provider_governance.py", "--review-template"])

    assert governance_main() == 0
    assert logging.root.manager.disable == previous_disable

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["kind"] == "provider-approval-review-template"
    assert payload["features"]
