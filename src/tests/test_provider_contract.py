from __future__ import annotations

from datetime import datetime

import pytest

from src.features.contracts import (
    ProviderContractError,
    ProviderResponse,
    validate_provider_response,
)


def test_provider_response_contract_accepts_safe_fixture_envelope():
    response = ProviderResponse(
        data={"content": "fixture"},
        source="Replay fixture",
        source_url="https://fixture.invalid/source",
    )

    validate_provider_response(response, provider_name="fixture")


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (
            ProviderResponse(data={"content": "x"}, source="", source_url=None),
            "source_missing",
        ),
        (
            ProviderResponse(
                data={"content": "x"},
                source="fixture",
                source_url="https://user:pass@example.invalid/source",
            ),
            "source_url_credentials",
        ),
        (
            ProviderResponse(
                data={"content": "x"},
                source="fixture",
                source_url="https://fixture.invalid/source",
                fetched_at=datetime(2026, 1, 1),
            ),
            "fetched_at_timezone_missing",
        ),
        (
            ProviderResponse(data=None, source="fixture", source_url=None),
            "data_missing",
        ),
    ],
)
def test_provider_response_contract_rejects_unsafe_or_incomplete_envelopes(response, reason):
    with pytest.raises(ProviderContractError) as error:
        validate_provider_response(response, provider_name="fixture")

    assert error.value.reason == reason
