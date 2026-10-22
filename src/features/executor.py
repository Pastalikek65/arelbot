"""Execute registered features through the common provider envelope."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from .contracts import FeatureResult, ProviderContractError, ProviderRequest, ProviderResponse
from .registry import FeatureRegistry
from .runtime import ProviderRateLimitExceeded, ProviderRuntime
from src.security.log_redaction import summarize_exception
from src.security.error_codes import normalize_error_code
from src.observability.metrics import MetricsRegistry


_KNOWN_PROVIDER_FAILURES = {
    "PROVIDER_RESPONSE_TOO_LARGE": "Kaynak yanıtı izin verilen boyutu aşıyor.",
    "PROVIDER_CONTENT_TYPE_INVALID": "Kaynak beklenen içerik türünü göndermedi.",
    "PROVIDER_HOST_NOT_ALLOWED": "Kaynak güvenlik politikası nedeniyle reddedildi.",
    "PROVIDER_SCHEMA_ERROR": "Kaynak beklenmeyen bir veri biçimi döndürdü.",
}


def _stable_provider_failure(exc: Exception) -> tuple[str, str]:
    """Map only known provider policy identifiers to safe user errors."""

    raw = str(exc).strip().split(":", 1)[0].strip().upper()
    if raw in {
        "PROVIDER_RESPONSE_TOO_LARGE",
        "PROVIDER_CONTENT_TYPE_INVALID",
        "PROVIDER_HOST_NOT_ALLOWED",
    }:
        code = raw
    elif raw.endswith("_SCHEMA_ERROR") and raw.startswith("PROVIDER_"):
        code = "PROVIDER_SCHEMA_ERROR"
    else:
        code = "PROVIDER_UNAVAILABLE"
    return code, _KNOWN_PROVIDER_FAILURES.get(code, "Veri sağlayıcısı şu an kullanılamıyor.")


class FeatureExecutor:
    MAX_HEALTH_WRITE_TASKS = 128

    def __init__(self, registry: FeatureRegistry):
        self.registry = registry
        self._runtimes: dict[tuple[str, int], ProviderRuntime] = {}
        self._shared_cache: Any | None = None
        self._health_store: Any | None = None
        self._health_write_tasks: set[asyncio.Task[Any]] = set()
        self._health_writes_dropped = 0
        self.metrics = MetricsRegistry(sample_limit=1000)

    def attach_shared_cache(self, cache: Any | None) -> None:
        """Attach the process-wide Redis client after application startup."""
        self._shared_cache = cache
        for runtime in self._runtimes.values():
            runtime.shared_cache = cache

    def attach_health_store(self, store: Any | None) -> None:
        """Attach a repository exposing ``save_provider_health``.

        Keeping this as a small protocol-like dependency means tests can use a
        memory sink while production uses the Mongo repository.  The executor
        never reaches into Mongo collections directly.
        """
        self._health_store = store

    async def _persist_health(
        self,
        provider_name: str,
        runtime: ProviderRuntime,
        response: ProviderResponse[Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        if self._health_store is None or not hasattr(self._health_store, "save_provider_health"):
            return
        try:
            payload = asdict(runtime.health())
            payload["provider"] = provider_name
            payload["updated_at"] = datetime.now(timezone.utc)
            if response is not None:
                payload.update(
                    {
                        "source": response.source,
                        "source_url": response.source_url,
                        "last_fetched_at": response.fetched_at,
                        "last_cache_hit": response.cache_hit,
                        "last_stale": response.stale,
                        "last_metadata": dict(response.metadata),
                    }
                )
            if error_code:
                payload["last_error_code"] = error_code
            await self._health_store.save_provider_health(payload)
        except Exception:
            # Health persistence is observability; it must never break a user
            # request when Mongo is unavailable or a test sink is incomplete.
            return

    def _schedule_health_persistence(
        self,
        provider_name: str,
        runtime: ProviderRuntime,
        response: ProviderResponse[Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        """Persist health out of band so an observability sink cannot slow users.

        Mongo health writes are operational telemetry, not part of the feature
        result transaction. A bounded task set prevents an unavailable
        database from turning high request volume into unbounded task growth.
        Runtime health remains available in process even when persistence is
        dropped or delayed.
        """

        if self._health_store is None or not hasattr(self._health_store, "save_provider_health"):
            return
        if len(self._health_write_tasks) >= self.MAX_HEALTH_WRITE_TASKS:
            self._health_writes_dropped += 1
            return
        task = asyncio.create_task(
            self._persist_health(provider_name, runtime, response, error_code),
            name=f"arel-provider-health:{provider_name}",
        )
        self._health_write_tasks.add(task)
        task.add_done_callback(self._health_write_tasks.discard)

    def persistence_metrics(self) -> dict[str, int]:
        """Expose bounded telemetry-writer state without provider/user data."""

        return {
            "pending_health_writes": len(self._health_write_tasks),
            "dropped_health_writes": self._health_writes_dropped,
        }

    async def close(self, *, timeout_seconds: float = 3.0) -> None:
        """Drain queued best-effort writes before the database client closes."""

        tasks = tuple(self._health_write_tasks)
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=max(0.0, timeout_seconds))
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if done:
                await asyncio.gather(*done, return_exceptions=True)
        # Provider clients are closed by the composition root after the
        # executor.  Cancel detached stale-refresh tasks first so no provider
        # can start new I/O while its transport is being torn down.
        runtimes = tuple(self._runtimes.values())
        if runtimes:
            await asyncio.gather(
                *(runtime.close() for runtime in runtimes),
                return_exceptions=True,
            )

    def _runtime_for(
        self,
        provider_name: str,
        cache_ttl: int,
        rate_limit_per_minute: int | None = None,
    ) -> ProviderRuntime:
        runtime_key = (provider_name, max(0, cache_ttl))
        runtime = self._runtimes.get(runtime_key)
        if runtime is None:
            provider = self.registry.get_provider(provider_name)
            if provider is None:
                raise KeyError(f"Provider not registered: {provider_name}")
            runtime = ProviderRuntime(
                provider,
                cache_ttl_seconds=cache_ttl,
                rate_limit_per_minute=rate_limit_per_minute,
                shared_cache=self._shared_cache,
            )
            self._runtimes[runtime_key] = runtime
        return runtime

    async def execute(self, feature_key: str, params: dict[str, Any] | None = None) -> FeatureResult[Any]:
        """Execute a feature and record bounded request/result latency metrics."""

        started = time.perf_counter()
        metric_key = str(feature_key or "unknown")[:80] if self.registry.get_feature(feature_key) else "unknown"
        self.metrics.inc("feature_requests_total")
        try:
            result = await self._execute(feature_key, params)
            self.metrics.inc("feature_success_total" if result.ok else "feature_errors_total")
            return result
        except Exception:
            self.metrics.inc("feature_errors_total")
            raise
        finally:
            elapsed = time.perf_counter() - started
            self.metrics.observe("feature_execution_seconds", elapsed)
            self.metrics.observe(f"feature_execution_seconds_{metric_key}", elapsed)

    async def _execute(self, feature_key: str, params: dict[str, Any] | None = None) -> FeatureResult[Any]:
        feature = self.registry.get_feature(feature_key)
        if feature is None:
            return FeatureResult.failure("FEATURE_NOT_FOUND", "Feature bulunamad\u0131.")
        if not self.registry.feature_enabled(feature_key):
            status = self.registry.feature_status(feature_key)
            return FeatureResult.failure(
                "FEATURE_DISABLED",
                "Bu özellik geçici olarak kullanıma kapatıldı.",
                metadata={
                    "feature_key": feature_key,
                    "feature_flag": status.get("flag"),
                    "reason": status.get("reason"),
                },
            )
        if not feature.definition.provider_key:
            return FeatureResult.failure("PROVIDER_NOT_CONFIGURED", "Feature provider yap\u0131land\u0131r\u0131lmam\u0131\u015f.")
        try:
            runtime = self._runtime_for(
                feature.definition.provider_key,
                feature.definition.cache_ttl_seconds,
                feature.definition.rate_limit_per_minute,
            )
        except KeyError:
            return FeatureResult.failure(
                "PROVIDER_NOT_FOUND",
                "Feature saglayicisi kayitli degil.",
            )
        provider = self.registry.get_provider(feature.definition.provider_key)
        if provider is None:
            return FeatureResult.failure(
                "PROVIDER_NOT_FOUND",
                "Feature saglayicisi kayitli degil.",
            )
        request = ProviderRequest(
            operation=feature_key,
            params=params or {},
            timeout_seconds=max(0.1, feature.definition.timeout_ms / 1000),
        )
        try:
            response = await runtime.fetch(request)
        except ProviderRateLimitExceeded as exc:
            self._schedule_health_persistence(
                feature.definition.provider_key,
                runtime,
                error_code="PROVIDER_RATE_LIMITED",
            )
            return FeatureResult.failure(
                "PROVIDER_RATE_LIMITED",
                "Veri kaynağı yoğun; lütfen kısa süre sonra tekrar deneyin.",
                correlation_id=request.correlation_id,
                metadata={"retry_after_seconds": round(exc.retry_after_seconds, 2)},
            )
        except ProviderContractError as exc:
            self._schedule_health_persistence(
                feature.definition.provider_key,
                runtime,
                error_code="PROVIDER_SCHEMA_INVALID",
            )
            return FeatureResult.failure(
                "PROVIDER_SCHEMA_INVALID",
                "Veri sağlayıcısının yanıtı güvenli biçimde doğrulanamadı.",
                correlation_id=request.correlation_id,
                metadata={"contract_error": exc.reason},
            )
        except ValueError as exc:
            code = normalize_error_code(exc, "INVALID_FEATURE_INPUT")
            self._schedule_health_persistence(feature.definition.provider_key, runtime, error_code=code)
            return FeatureResult.failure(
                code,
                "İstek bilgilerini kontrol edin.",
                correlation_id=request.correlation_id,
            )
        except Exception as exc:
            error_code, error_message = _stable_provider_failure(exc)
            self._schedule_health_persistence(feature.definition.provider_key, runtime, error_code=error_code)
            return FeatureResult.failure(
                error_code,
                error_message,
                correlation_id=request.correlation_id,
                metadata={"detail": summarize_exception(exc, label="provider_failure")},
            )
        try:
            # Normalization is part of the provider contract.  It runs after
            # runtime cache/fallback selection so every caller receives the
            # same stable schema, including cached and stale responses.
            raw = await provider.normalize(response.data)
        except ValueError as exc:
            code = normalize_error_code(exc, "PROVIDER_SCHEMA_INVALID")
            self._schedule_health_persistence(feature.definition.provider_key, runtime, response, error_code=code)
            return FeatureResult.failure(
                code,
                "Veri sağlayıcısının yanıtı beklenen biçimde değil.",
                correlation_id=request.correlation_id,
            )
        except Exception:
            self._schedule_health_persistence(
                feature.definition.provider_key,
                runtime,
                response,
                error_code="PROVIDER_NORMALIZATION_FAILED",
            )
            return FeatureResult.failure(
                "PROVIDER_SCHEMA_INVALID",
                "Veri sağlayıcısının yanıtı işlenemedi.",
                correlation_id=request.correlation_id,
            )
        if isinstance(raw, dict) and raw.get("error"):
            self._schedule_health_persistence(feature.definition.provider_key, runtime, response, error_code="PROVIDER_ERROR")
            return FeatureResult.failure(
                normalize_error_code(raw.get("error_code") or raw.get("error"), "PROVIDER_ERROR"),
                "Veri kaynağı güvenli bir yanıt üretemedi.",
                correlation_id=request.correlation_id,
            )
        display = ()
        if isinstance(raw, dict):
            value = raw.get("content") or raw.get("result")
            if value:
                display = (str(value),)
        elif raw is not None:
            display = (str(raw),)
        # A provider may select a governed fallback at runtime (for example,
        # DuckDuckGo when Tavily has no key). Preserve that provider's
        # attribution instead of claiming the primary source was queried.
        provider_selected_fallback = bool(response.metadata.get("fallback"))
        if (feature.definition.source_name or feature.definition.source_url) and not provider_selected_fallback:
            response = ProviderResponse(
                data=response.data,
                source=feature.definition.source_name or response.source,
                source_url=feature.definition.source_url or response.source_url,
                fetched_at=response.fetched_at,
                stale=response.stale,
                cache_hit=response.cache_hit,
                metadata=response.metadata,
            )
        age_seconds = max(0.0, (datetime.now(timezone.utc) - response.fetched_at).total_seconds())
        metadata = {
            **dict(response.metadata),
            "feature_key": feature.definition.key,
            "freshness_sla_seconds": feature.definition.freshness_sla_seconds,
            "source_license": feature.definition.source_license,
            "source_terms_url": feature.definition.source_terms_url,
            "attribution_required": feature.definition.attribution_required,
            "age_seconds": age_seconds,
            "fresh": not response.stale and (
                feature.definition.freshness_sla_seconds <= 0
                or age_seconds <= feature.definition.freshness_sla_seconds
            ),
        }
        self._schedule_health_persistence(feature.definition.provider_key, runtime, response)
        return FeatureResult.success(
            raw,
            display_blocks=display,
            response=ProviderResponse(
                data=response.data,
                source=response.source,
                source_url=response.source_url,
                fetched_at=response.fetched_at,
                stale=response.stale,
                cache_hit=response.cache_hit,
                metadata=metadata,
            ),
            correlation_id=request.correlation_id,
            metadata=metadata,
        )

    def metrics_snapshot(self) -> dict[str, Any]:
        """Return feature counters and p50/p95/p99 latency samples."""

        return self.metrics.snapshot()

    def clear_cache(self) -> None:
        for runtime in self._runtimes.values():
            runtime.clear_cache()

    def reset_circuits(self) -> None:
        for runtime in self._runtimes.values():
            runtime.reset_circuit()

    async def health_snapshot(self) -> list[Any]:
        """Combine registry checks with observed runtime statistics."""
        checks = {item.name: item for item in await self.registry.health_snapshot()}
        for runtime in self._runtimes.values():
            observed = runtime.health()
            current = checks.get(observed.name)
            if current is None:
                checks[observed.name] = observed
            else:
                # A live registry check can be healthy while recent calls are
                # failing; observed runtime state is the stricter signal.
                from .contracts import ProviderHealth
                checks[observed.name] = ProviderHealth(
                    name=observed.name,
                    healthy=current.healthy and observed.healthy,
                    latency_ms=observed.latency_ms or current.latency_ms,
                    error_rate=observed.error_rate,
                    last_success_at=observed.last_success_at or current.last_success_at,
                    disabled_until=observed.disabled_until,
                    detail=observed.detail or current.detail,
                    score=observed.score if observed.score is not None else current.score,
                )
        return list(checks.values())
