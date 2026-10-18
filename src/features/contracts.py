"""Shared contracts for user-facing features and external data providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generic, Mapping, Protocol, TypeVar
from uuid import uuid4
from urllib.parse import urlparse


T = TypeVar("T")


class ProviderContractError(RuntimeError):
    """Raised when an upstream adapter violates the stable response envelope."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ProviderRequest:
    """Provider input with a deadline and correlation identifier."""

    operation: str
    params: Mapping[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 8.0
    correlation_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class ProviderHealth:
    name: str
    healthy: bool
    latency_ms: float | None = None
    error_rate: float | None = None
    last_success_at: datetime | None = None
    disabled_until: datetime | None = None
    detail: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class ProviderResponse(Generic[T]):
    data: T
    source: str
    source_url: str | None
    fetched_at: datetime = field(default_factory=utc_now)
    stale: bool = False
    cache_hit: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


def validate_provider_response(response: ProviderResponse[Any], *, provider_name: str) -> None:
    """Validate the provider envelope before it enters cache or fallback flow.

    The validator deliberately checks transport-level invariants only. Feature
    schema validation remains the provider's responsibility because different
    typed features have different payload shapes.
    """

    if not isinstance(response, ProviderResponse):
        raise ProviderContractError("response_type_invalid")
    if not str(response.source or "").strip():
        raise ProviderContractError("source_missing")
    source_url = response.source_url
    if source_url is not None:
        parsed = urlparse(str(source_url))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderContractError("source_url_invalid")
        if parsed.username or parsed.password:
            raise ProviderContractError("source_url_credentials")
    if response.data is None:
        raise ProviderContractError("data_missing")
    if response.fetched_at.tzinfo is None or response.fetched_at.utcoffset() is None:
        raise ProviderContractError("fetched_at_timezone_missing")
    if not isinstance(response.metadata, Mapping):
        raise ProviderContractError("metadata_invalid")


@dataclass(frozen=True)
class FeatureResult(Generic[T]):
    """Stable result envelope consumed by Telegram and AI adapters."""

    ok: bool
    data: T | None = None
    display_blocks: tuple[str, ...] = ()
    source: str | None = None
    source_url: str | None = None
    fetched_at: datetime | None = None
    stale: bool = False
    cache_hit: bool = False
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    error_code: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        data: T,
        *,
        display_blocks: tuple[str, ...] = (),
        response: ProviderResponse[Any] | None = None,
        correlation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "FeatureResult[T]":
        return cls(
            ok=True,
            data=data,
            display_blocks=display_blocks,
            source=response.source if response else None,
            source_url=response.source_url if response else None,
            fetched_at=response.fetched_at if response else utc_now(),
            stale=response.stale if response else False,
            cache_hit=response.cache_hit if response else False,
            correlation_id=correlation_id or (uuid4().hex),
            metadata=metadata or (response.metadata if response else {}),
        )

    @classmethod
    def failure(
        cls,
        error_code: str,
        error_message: str,
        *,
        correlation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "FeatureResult[Any]":
        return cls(
            ok=False,
            error_code=error_code,
            error_message=error_message,
            correlation_id=correlation_id or uuid4().hex,
            metadata=metadata or {},
        )


@dataclass(frozen=True)
class FeatureDefinition:
    key: str
    category: str
    commands: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    input_model: str | None = None
    output_type: str = "text"
    permission: str = "user"
    provider_key: str | None = None
    # ``provider_key`` may identify either a typed data provider or an AI
    # route.  Keeping the kind explicit prevents provider audits from treating
    # an AI model name as a ``DataProvider`` registry entry.
    provider_kind: str = "data"
    cache_ttl_seconds: int = 0
    freshness_sla_seconds: int = 0
    timeout_ms: int = 5000
    async_mode: bool = False
    cost_class: str = "free"
    feature_flag: str | None = None
    audit_policy: str = "none"
    source_name: str | None = None
    source_url: str | None = None
    source_license: str | None = None
    source_terms_url: str | None = None
    attribution_required: bool = True
    rate_limit_per_minute: int | None = None
    # Source governance is explicit so a technically working adapter cannot
    # be mistaken for a production-approved data source.
    source_governance: str = "not_required"
    robots_policy: str = "not_applicable"
    terms_review: str = "not_applicable"
    production_signoff: str = "not_required"
    # Optional durable-state metadata.  Board games use the same fields as
    # GameDefinition so the runtime manifest and persisted sessions cannot
    # silently drift apart.
    schema_version: int = 1
    state_key: str | None = None
    board_size: tuple[int, int] | None = None
    connect_length: int | None = None


class DataProvider(Protocol):
    name: str
    capabilities: set[str]

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[Any]: ...

    async def normalize(self, raw: Any) -> Any: ...

    async def health_check(self) -> ProviderHealth: ...
