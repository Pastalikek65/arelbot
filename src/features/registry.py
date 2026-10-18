"""Feature and provider registry.

The registry is intentionally small and synchronous at lookup time.  It is
safe to use from command handlers, callback handlers and AI tool preparation.
Actual provider calls remain asynchronous and live outside Telegram routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
import asyncio

from .contracts import DataProvider, FeatureDefinition
from .provider_governance import (
    ProviderApproval,
    approval_matches,
    is_external_source,
    is_production_approved,
)
from src.security.log_redaction import summarize_exception


@dataclass
class RegisteredFeature:
    definition: FeatureDefinition
    handler: Any


class FeatureRegistry:
    def __init__(
        self,
        *,
        feature_flags: Mapping[str, bool] | None = None,
        disabled_features: set[str] | frozenset[str] | None = None,
        require_provider_approval: bool = False,
        provider_approvals: Mapping[str, ProviderApproval] | None = None,
    ) -> None:
        self._features: dict[str, RegisteredFeature] = {}
        self._providers: dict[str, DataProvider] = {}
        self._feature_flags = {
            str(key).strip().casefold(): bool(value)
            for key, value in (feature_flags or {}).items()
            if str(key).strip()
        }
        self._disabled_features = {
            str(key).strip().casefold()
            for key in (disabled_features or set())
            if str(key).strip()
        }
        self._require_provider_approval = bool(require_provider_approval)
        self._provider_approvals = {
            str(key).strip(): value
            for key, value in (provider_approvals or {}).items()
            if str(key).strip() and isinstance(value, ProviderApproval)
        }

    def register_feature(self, definition: FeatureDefinition, handler: Any) -> None:
        if definition.key in self._features:
            raise ValueError(f"Feature already registered: {definition.key}")
        self._features[definition.key] = RegisteredFeature(definition, handler)

    def feature_enabled(self, key: str) -> bool:
        """Return whether a feature is available under the configured kill switch."""
        item = self._features.get(key)
        if item is None:
            return False
        normalized_key = str(key).strip().casefold()
        flag_name = str(item.definition.feature_flag or key).strip().casefold()
        approval = self._provider_approvals.get(item.definition.key)
        if (
            self._require_provider_approval
            and is_external_source(item.definition)
            and not is_production_approved(item.definition, approval)
        ):
            return False
        if normalized_key in self._disabled_features or flag_name in self._disabled_features:
            return False
        if flag_name in self._feature_flags:
            return self._feature_flags[flag_name]
        if normalized_key in self._feature_flags:
            return self._feature_flags[normalized_key]
        return True

    def feature_status(self, key: str) -> dict[str, Any]:
        item = self._features.get(key)
        if item is None:
            return {"key": key, "enabled": False, "reason": "not_registered"}
        flag_name = str(item.definition.feature_flag or key).strip().casefold()
        enabled = self.feature_enabled(key)
        approval = self._provider_approvals.get(item.definition.key)
        approval_valid = approval_matches(item.definition, approval)
        explicitly_disabled = (
            str(key).strip().casefold() in self._disabled_features
            or flag_name in self._disabled_features
            or self._feature_flags.get(str(key).strip().casefold()) is False
            or self._feature_flags.get(flag_name) is False
        )
        status = {
            "key": item.definition.key,
            "flag": flag_name,
            "enabled": enabled,
            "reason": (
                None
                if enabled
                else (
                    "kill_switch"
                    if explicitly_disabled
                    else (
                        "provider_governance"
                        if self._require_provider_approval
                        and is_external_source(item.definition)
                        and not is_production_approved(item.definition, approval)
                        else "kill_switch"
                    )
                )
            ),
            "provider_approval_required": self._require_provider_approval and is_external_source(item.definition),
        }
        # Keep the historical status shape for unapproved features while
        # exposing bounded evidence metadata once a valid review is present.
        if approval_valid and approval is not None:
            status.update(
                {
                    "provider_approval_id": approval.approval_id,
                    "provider_approval_expires_at": approval.expires_at.isoformat(),
                }
            )
        return status

    def feature_statuses(self) -> list[dict[str, Any]]:
        return [self.feature_status(item.definition.key) for item in self._features.values()]

    def governance_report(self, *, require_approved: bool | None = None) -> dict[str, Any]:
        """Return governance for the registry's active approval snapshot.

        Runtime callers must not reconstruct this report from the feature
        definitions alone: production approvals are loaded once during
        bootstrap and are bound to the registry.  Keeping this operation on
        the registry prevents an administrator view from accidentally
        dropping the active approval manifest.
        """
        from .provider_governance import governance_report

        enforced = self._require_provider_approval if require_approved is None else bool(require_approved)
        return governance_report(
            self,
            approvals=dict(self._provider_approvals),
            require_approved=enforced,
        )

    def runtime_toggle_supported(self, key: str) -> bool:
        """Whether the central provider kill-switch can govern ``key``.

        Local games, renderers and queue-owned workflows have their own
        lifecycle. They must not be shown as disabled while their execution
        path bypasses ``FeatureExecutor``. Provider-backed features are
        guarded centrally before any network call.
        """
        item = self._features.get(key)
        return bool(item and item.definition.provider_key)

    def set_feature_enabled(self, key: str, enabled: bool) -> bool:
        """Set an in-process emergency state for controlled admin tooling."""
        lookup_key = key if key in self._features else str(key).strip().casefold()
        if lookup_key not in self._features:
            return False
        normalized = str(lookup_key).strip().casefold()
        self._feature_flags[normalized] = bool(enabled)
        self._disabled_features.discard(normalized)
        if not enabled:
            self._disabled_features.add(normalized)
        return True

    def register_provider(self, provider: DataProvider) -> None:
        if provider.name in self._providers:
            raise ValueError(f"Provider already registered: {provider.name}")
        self._providers[provider.name] = provider

    def get_feature(self, key: str) -> RegisteredFeature | None:
        return self._features.get(key)

    def get_provider(self, name: str) -> DataProvider | None:
        return self._providers.get(name)

    def definitions(self) -> tuple[FeatureDefinition, ...]:
        return tuple(item.definition for item in self._features.values())

    def providers(self) -> tuple[DataProvider, ...]:
        return tuple(self._providers.values())

    def command_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for item in self._features.values():
            for command in (*item.definition.commands, *item.definition.aliases):
                mapping[command.lstrip("/")] = item.definition.key
        return mapping

    def manifest(self) -> list[dict[str, Any]]:
        return [definition.__dict__.copy() for definition in self.definitions()]

    async def health_snapshot(self) -> list[Any]:
        """Collect provider health without allowing one provider to block all others."""
        async def check(provider: DataProvider):
            try:
                return await asyncio.wait_for(provider.health_check(), timeout=2.0)
            except Exception as exc:  # provider health must never break /health
                from .contracts import ProviderHealth
                return ProviderHealth(
                    name=provider.name,
                    healthy=False,
                    detail=summarize_exception(exc, label="provider_health"),
                )

        return list(await asyncio.gather(*(check(provider) for provider in self.providers())))
