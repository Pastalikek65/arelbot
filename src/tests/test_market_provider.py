import pytest
import httpx

from src.features.contracts import ProviderRequest
from src.features.providers.market import MarketProvider


@pytest.mark.asyncio
async def test_market_provider_normalizes_alias_and_returns_safe_quote():
    def loader(symbol: str):
        assert symbol == "THYAO.IS"
        return {
            "rows": [
                {"time": "1", "close": 100.0, "high": 101.0, "low": 99.0},
                {"time": "2", "close": 105.0, "high": 106.0, "low": 100.0},
            ]
        }

    result = await MarketProvider(loader=loader).fetch(
        ProviderRequest(operation="market", params={"symbol": "thy"})
    )

    assert result.data["symbol"] == "THYAO.IS"
    assert result.data["price"] == 105.0
    assert result.data["change_pct"] == 5.0
    assert "THYAO.IS" in result.data["content"]
    assert result.source == "Yahoo Finance (Chart API)"


@pytest.mark.asyncio
async def test_market_provider_chart_api_replay_avoids_yfinance():
    payload = {
        "chart": {
            "result": [{
                "timestamp": [1700000000, 1700003600],
                "indicators": {"quote": [{
                    "close": [100.0, 102.5],
                    "high": [101.0, 103.0],
                    "low": [99.0, 102.0],
                }]},
            }],
            "error": None,
        }
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "query1.finance.yahoo.com"
        assert request.url.path.endswith("/THYAO.IS")
        return httpx.Response(200, json=payload, request=request)

    provider = MarketProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await provider.fetch(ProviderRequest(operation="market", params={"symbol": "THY"}))
    assert result.data["price"] == 102.5
    assert result.metadata["records"] == 2


@pytest.mark.asyncio
async def test_market_provider_rejects_invalid_or_empty_replay():
    with pytest.raises(ValueError, match="market_symbol_invalid"):
        await MarketProvider(loader=lambda _: {"rows": []}).fetch(
            ProviderRequest(operation="market", params={"symbol": "bad symbol"})
        )
    with pytest.raises(RuntimeError, match="market_no_data"):
        await MarketProvider(loader=lambda _: {"rows": []}).fetch(
            ProviderRequest(operation="market", params={"symbol": "THY"})
        )
