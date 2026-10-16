from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    transient_codes: frozenset[str] = frozenset({
        "timeout", "download_failed", "delivery_failed", "provider_unavailable",
        "probe_failed", "internal_error", "source_rate_limited",
        "source_access_restricted", "empty_result", "fallback_invalid_response",
    })

    def should_retry(self, error_code: str, attempt: int) -> bool:
        return error_code in self.transient_codes and attempt < self.max_attempts - 1

