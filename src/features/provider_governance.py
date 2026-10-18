"""Provider source-governance policy and release reporting.

This module is deliberately deterministic and offline.  It records whether a
source has had its terms, robots policy, and production approval reviewed; it
never attempts to infer legal permission from a successful HTTP response.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, TYPE_CHECKING

from .contracts import FeatureDefinition

if TYPE_CHECKING:
    from .registry import FeatureRegistry


SOURCE_GOVERNANCE_VALUES = frozenset({"approved", "pending", "disabled", "not_required"})
ROBOTS_POLICY_VALUES = frozenset({"allowed", "review_required", "disallowed", "not_applicable"})
TERMS_REVIEW_VALUES = frozenset({"approved", "review_required", "disallowed", "not_applicable"})
PRODUCTION_SIGNOFF_VALUES = frozenset({"approved", "required", "not_required", "rejected"})

APPROVAL_SCHEMA_VERSION = 1
MAX_APPROVAL_MANIFEST_BYTES = 1024 * 1024
_APPROVAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
_FEATURE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


class GovernanceApprovalError(ValueError):
    """Stable, secret-free error raised while loading approval evidence."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True)
class ProviderApproval:
    """A bounded, time-limited review record for one external feature.

    The record intentionally contains no credentials or response data.  The
    source fingerprint binds the review to the exact source identity in the
    feature manifest, so changing a URL, provider or terms record invalidates
    the approval instead of silently carrying it forward.
    """

    feature_key: str
    approval_id: str
    source_fingerprint: str
    reviewer: str
    issued_at: datetime
    expires_at: datetime
    source_governance: str = "approved"
    robots_policy: str = "allowed"
    terms_review: str = "approved"
    production_signoff: str = "approved"

    def active(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None or moment.utcoffset() is None:
            moment = moment.replace(tzinfo=timezone.utc)
        # Treat the expiry instant as closed: a deployment crossing that
        # boundary must re-review rather than receive one more request.
        return self.issued_at <= moment < self.expires_at

    def as_dict(self) -> dict[str, str]:
        """Return safe evidence metadata without source URLs or credentials."""

        return {
            "feature_key": self.feature_key,
            "approval_id": self.approval_id,
            "source_fingerprint": self.source_fingerprint,
            "reviewer": self.reviewer,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "source_governance": self.source_governance,
            "robots_policy": self.robots_policy,
            "terms_review": self.terms_review,
            "production_signoff": self.production_signoff,
        }


def source_fingerprint(definition: FeatureDefinition) -> str:
    """Hash the source identity which a human reviewer actually examined."""

    payload = {
        "feature_key": definition.key,
        "provider_key": definition.provider_key,
        "source_name": definition.source_name,
        "source_url": definition.source_url,
        "source_license": definition.source_license,
        "source_terms_url": definition.source_terms_url,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_approval_review_template(registry: FeatureRegistry) -> dict[str, Any]:
    """Build a safe worksheet for a human provider-governance review.

    The worksheet is intentionally *not* an approval manifest: it contains no
    approval IDs, reviewer identity or validity window and therefore cannot be
    loaded by :func:`load_approval_manifest`.  It does contain the exact source
    fingerprint that a completed approval must bind to, plus the current
    review fields.  URLs stay in the separately reviewed ``DATA_SOURCES``
    document and are never copied into an operator report or log.
    """

    rows: list[dict[str, Any]] = []
    for definition in sorted(registry.definitions(), key=lambda item: item.key):
        if not is_external_source(definition):
            continue
        rows.append(
            {
                "feature_key": definition.key,
                "provider_key": definition.provider_key,
                "source_name": definition.source_name,
                "source_fingerprint": source_fingerprint(definition),
                "current_source_governance": definition.source_governance,
                "current_robots_policy": definition.robots_policy,
                "current_terms_review": definition.terms_review,
                "current_production_signoff": definition.production_signoff,
                "review_required": not is_production_approved(definition),
            }
        )
    return {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "kind": "provider-approval-review-template",
        "ok": True,
        "features": rows,
    }


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise GovernanceApprovalError(f"approval_{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise GovernanceApprovalError(f"approval_{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GovernanceApprovalError(f"approval_{field}_timezone_missing")
    return parsed.astimezone(timezone.utc)


def _approval_from_row(row: object, *, now: datetime) -> ProviderApproval:
    if not isinstance(row, Mapping):
        raise GovernanceApprovalError("approval_row_invalid")
    feature_key = str(row.get("feature_key") or "").strip()
    approval_id = str(row.get("approval_id") or "").strip()
    fingerprint = str(row.get("source_fingerprint") or "").strip().lower()
    reviewer = str(row.get("reviewer") or "").strip()
    if not _FEATURE_KEY_RE.fullmatch(feature_key):
        raise GovernanceApprovalError("approval_feature_key_invalid")
    if not _APPROVAL_ID_RE.fullmatch(approval_id):
        raise GovernanceApprovalError("approval_id_invalid")
    if not _FINGERPRINT_RE.fullmatch(fingerprint):
        raise GovernanceApprovalError("approval_fingerprint_invalid")
    if not reviewer or len(reviewer) > 120:
        raise GovernanceApprovalError("approval_reviewer_invalid")
    issued_at = _parse_timestamp(row.get("issued_at"), "issued_at")
    expires_at = _parse_timestamp(row.get("expires_at"), "expires_at")
    if expires_at <= issued_at:
        raise GovernanceApprovalError("approval_window_invalid")
    # Annual re-review is a deliberate upper bound.  Long-lived approvals are
    # too easy to forget when a source changes its terms.
    if expires_at - issued_at > timedelta(days=366):
        raise GovernanceApprovalError("approval_window_too_long")
    if issued_at > now + timedelta(minutes=5):
        raise GovernanceApprovalError("approval_issued_in_future")
    if expires_at <= now:
        raise GovernanceApprovalError("approval_expired")
    statuses = {
        "source_governance": str(row.get("source_governance") or "").strip(),
        "robots_policy": str(row.get("robots_policy") or "").strip(),
        "terms_review": str(row.get("terms_review") or "").strip(),
        "production_signoff": str(row.get("production_signoff") or "").strip(),
    }
    if statuses != {
        "source_governance": "approved",
        "robots_policy": "allowed",
        "terms_review": "approved",
        "production_signoff": "approved",
    }:
        raise GovernanceApprovalError("approval_status_incomplete")
    return ProviderApproval(
        feature_key=feature_key,
        approval_id=approval_id,
        source_fingerprint=fingerprint,
        reviewer=reviewer[:120],
        issued_at=issued_at,
        expires_at=expires_at,
        **statuses,
    )


def load_approval_manifest(path: str | Path, *, now: datetime | None = None) -> dict[str, ProviderApproval]:
    """Load a strict, bounded approval file without exposing its contents.

    The file is operator-controlled deployment evidence, not a secret.  We
    still reject symlinked paths and group/world-writable files on POSIX so a
    compromised working directory cannot replace an approval after startup.
    """

    raw_path = Path(path).expanduser()
    if not raw_path.is_file() or raw_path.is_symlink():
        raise GovernanceApprovalError("approval_manifest_missing")
    for parent in (raw_path.parent, *raw_path.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise GovernanceApprovalError("approval_manifest_parent_symlink")
    try:
        if raw_path.stat().st_size > MAX_APPROVAL_MANIFEST_BYTES:
            raise GovernanceApprovalError("approval_manifest_too_large")
        if os.name != "nt" and raw_path.stat().st_mode & 0o077:
            raise GovernanceApprovalError("approval_manifest_permissions_too_open")
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
    except GovernanceApprovalError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise GovernanceApprovalError("approval_manifest_invalid") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != APPROVAL_SCHEMA_VERSION:
        raise GovernanceApprovalError("approval_schema_unsupported")
    rows = payload.get("approvals")
    if not isinstance(rows, list) or len(rows) > 256:
        raise GovernanceApprovalError("approval_rows_invalid")
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        moment = moment.replace(tzinfo=timezone.utc)
    approvals: dict[str, ProviderApproval] = {}
    for row in rows:
        approval = _approval_from_row(row, now=moment)
        if approval.feature_key in approvals:
            raise GovernanceApprovalError("approval_feature_duplicate")
        approvals[approval.feature_key] = approval
    return approvals


def is_external_source(definition: FeatureDefinition) -> bool:
    """Return whether a definition needs source-governance review."""

    return bool(
        definition.cost_class == "network"
        or definition.source_url
        or definition.source_governance != "not_required"
    )


def approval_matches(
    definition: FeatureDefinition,
    approval: ProviderApproval | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether an external review matches the current source identity."""

    if approval is None or not is_external_source(definition):
        return False
    if definition.source_governance == "disabled":
        return False
    if approval.feature_key != definition.key or approval.source_fingerprint != source_fingerprint(definition):
        return False
    if not approval.active(now):
        return False
    # The adapter's structural metadata must still be valid.  The approval
    # record is allowed to elevate only the reviewed status fields; it cannot
    # hide a malformed source URL, missing terms metadata, or invalid schema.
    return not validate_governance(definition, require_approved=False)


def governance_status(
    definition: FeatureDefinition,
    approval: ProviderApproval | None = None,
) -> str:
    """Return a stable operational status without making an approval claim."""

    if not is_external_source(definition):
        return "not_required"
    if definition.source_governance not in SOURCE_GOVERNANCE_VALUES:
        return "invalid"
    if definition.source_governance == "approved" and is_production_approved(definition):
        return "approved"
    if approval_matches(definition, approval):
        return "approved"
    if definition.source_governance == "disabled":
        return "disabled"
    return "pending"


def validate_governance(
    definition: FeatureDefinition,
    *,
    require_approved: bool = False,
) -> tuple[str, ...]:
    """Validate source-governance metadata and optionally enforce sign-off.

    ``require_approved`` is intended for a production preflight.  The normal
    offline release gate permits ``pending`` records so a deployment can show
    exactly which feature still needs legal/robots review rather than silently
    dropping it from the inventory.
    """

    if not is_external_source(definition):
        return ()
    errors: list[str] = []
    if definition.source_governance not in SOURCE_GOVERNANCE_VALUES:
        errors.append("source_governance_invalid")
    if definition.robots_policy not in ROBOTS_POLICY_VALUES:
        errors.append("robots_policy_invalid")
    if definition.terms_review not in TERMS_REVIEW_VALUES:
        errors.append("terms_review_invalid")
    if definition.production_signoff not in PRODUCTION_SIGNOFF_VALUES:
        errors.append("production_signoff_invalid")

    # ``not_required``/``not_applicable`` are valid for local features, but
    # cannot silently stand in for a review on an external source.
    if definition.source_governance == "not_required":
        errors.append("source_governance_missing")
    if definition.robots_policy == "not_applicable":
        errors.append("robots_policy_missing")
    if definition.terms_review == "not_applicable":
        errors.append("terms_review_missing")
    if definition.production_signoff == "not_required":
        errors.append("production_signoff_missing")

    if definition.source_governance == "approved":
        if definition.robots_policy not in {"allowed", "not_applicable"}:
            errors.append("robots_review_missing")
        if definition.terms_review not in {"approved", "not_applicable"}:
            errors.append("terms_review_missing")
        if definition.production_signoff not in {"approved", "not_required"}:
            errors.append("production_signoff_missing")
    if require_approved and definition.source_governance != "approved":
        errors.append("source_governance_not_approved")
    return tuple(dict.fromkeys(errors))


def is_production_approved(
    definition: FeatureDefinition,
    approval: ProviderApproval | None = None,
) -> bool:
    """Return true only when every external governance field is complete."""

    return is_external_source(definition) and (
        not validate_governance(definition, require_approved=True)
        or approval_matches(definition, approval)
    )


def governance_report(
    registry: FeatureRegistry,
    *,
    require_approved: bool = False,
    approvals: Mapping[str, ProviderApproval] | None = None,
    disabled_features: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Build a safe, machine-readable report without source URLs.

    A production deployment may explicitly disable a pending external feature
    while its source review is outstanding.  That feature is then reported as
    ``disabled`` and does not need an approval record; every enabled external
    feature still requires the normal fingerprint-bound approval.  Unknown
    disable names are errors so a typo cannot silently leave a provider active.
    """

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    pending_count = 0
    disabled_count = 0
    approved_count = 0
    approvals = approvals or {}
    disabled = {
        str(key).strip().casefold()
        for key in (disabled_features or set())
        if str(key).strip()
    }
    known_keys = {definition.key for definition in registry.definitions()}
    known_keys_casefold = {key.casefold() for key in known_keys}
    for approval_key in approvals:
        if approval_key not in known_keys:
            errors.append(f"{approval_key}:approval_feature_unknown")
    for disabled_key in sorted(disabled - known_keys_casefold):
        errors.append(f"{disabled_key}:disabled_feature_unknown")
    for definition in registry.definitions():
        if not is_external_source(definition):
            continue
        approval = approvals.get(definition.key)
        disabled_by_config = definition.key.casefold() in disabled
        status = "disabled" if disabled_by_config else governance_status(definition, approval)
        if status == "pending":
            pending_count += 1
        elif status == "disabled":
            disabled_count += 1
        elif status == "approved":
            approved_count += 1
        enforce_approval = (
            require_approved
            and not disabled_by_config
            and not approval_matches(definition, approval)
        )
        for error in validate_governance(definition, require_approved=enforce_approval):
            errors.append(f"{definition.key}:{error}")
        approval_valid = approval_matches(definition, approval)
        row = {
            "feature_key": definition.key,
            "provider_key": definition.provider_key,
            "source_name": definition.source_name,
            "status": status,
            "robots_policy": approval.robots_policy if approval_valid and approval else definition.robots_policy,
            "terms_review": approval.terms_review if approval_valid and approval else definition.terms_review,
            "production_signoff": approval.production_signoff if approval_valid and approval else definition.production_signoff,
        }
        if disabled_by_config:
            row["disabled_by_config"] = True
        if approval_valid and approval:
            row.update(
                {
                    "approval_id": approval.approval_id,
                    "approval_expires_at": approval.expires_at.isoformat(),
                }
            )
        rows.append(row)
    return {
        "ok": not errors,
        "features": rows,
        "feature_count": len(rows),
        "approved_count": approved_count,
        "pending_count": pending_count,
        "disabled_count": disabled_count,
        "errors": errors,
    }
