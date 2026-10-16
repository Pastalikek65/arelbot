"""Static/provider-registry certification used by release gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

from .provider_governance import governance_status, validate_governance
from .registry import FeatureRegistry


@dataclass(frozen=True)
class ProviderAuditRow:
    feature_key: str
    provider_key: str
    provider_kind: str
    provider_name: str | None
    source_name: str | None
    source_url: str | None
    timeout_ms: int
    cache_ttl_seconds: int
    freshness_sla_seconds: int
    rate_limit_per_minute: int | None
    source_governance: str
    robots_policy: str
    terms_review: str
    production_signoff: str
    governance_status: str
    status: str
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def audit_registry(registry: FeatureRegistry) -> tuple[ProviderAuditRow, ...]:
    """Validate every network-backed feature against its typed provider.

    Local features intentionally have no provider key and are outside this
    audit. The result is deterministic and never calls an upstream service.
    """
    rows: list[ProviderAuditRow] = []
    for registered in registry.definitions():
        definition = registered
        provider_key = definition.provider_key
        if not provider_key:
            continue
        provider_kind = str(getattr(definition, "provider_kind", "data") or "data").strip().casefold()
        errors: list[str] = []
        if provider_kind not in {"data", "ai"}:
            errors.append("provider_kind_invalid")
        provider = registry.get_provider(provider_key) if provider_kind == "data" else None
        if provider_kind == "data":
            if provider is None:
                errors.append("provider_missing")
            else:
                for method in ("fetch", "normalize", "health_check"):
                    if not callable(getattr(provider, method, None)):
                        errors.append(f"provider_{method}_missing")
                if not str(getattr(provider, "name", "")).strip():
                    errors.append("provider_name_missing")
        if definition.timeout_ms <= 0:
            errors.append("timeout_missing")
        if definition.cache_ttl_seconds < 0 or definition.freshness_sla_seconds < 0:
            errors.append("cache_or_freshness_invalid")
        if definition.rate_limit_per_minute is not None and definition.rate_limit_per_minute <= 0:
            errors.append("rate_limit_invalid")
        if definition.cost_class == "network":
            if not definition.source_name:
                errors.append("source_name_missing")
            if not definition.source_license:
                errors.append("source_license_missing")
            if not definition.source_terms_url:
                errors.append("source_terms_missing")
        errors.extend(validate_governance(definition))
        if definition.source_url:
            parsed = urlparse(definition.source_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                errors.append("source_url_invalid")
        rows.append(
            ProviderAuditRow(
                feature_key=definition.key,
                provider_key=provider_key,
                provider_kind=provider_kind,
                provider_name=getattr(provider, "name", None) if provider else None,
                source_name=definition.source_name,
                source_url=definition.source_url,
                timeout_ms=definition.timeout_ms,
                cache_ttl_seconds=definition.cache_ttl_seconds,
                freshness_sla_seconds=definition.freshness_sla_seconds,
                rate_limit_per_minute=definition.rate_limit_per_minute,
                source_governance=definition.source_governance,
                robots_policy=definition.robots_policy,
                terms_review=definition.terms_review,
                production_signoff=definition.production_signoff,
                governance_status=governance_status(definition),
                status="ok" if not errors else "error",
                errors=tuple(errors),
            )
        )
    return tuple(rows)


def audit_errors(registry: FeatureRegistry) -> list[str]:
    """Return compact, stable error strings for CLI/CI output."""
    return [
        f"{row.feature_key}:{error}"
        for row in audit_registry(registry)
        if row.status != "ok"
        for error in row.errors
    ]
