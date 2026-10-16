import pytest

from src.services.market_chart_service import MarketChartError, MarketChartService


@pytest.mark.asyncio
async def test_market_chart_service_renders_bounded_fixture():
    service = MarketChartService(max_concurrency=1, timeout_seconds=10)
    output = await service.render({"symbol": "THYAO.IS", "rows": [{"close": 100}, {"close": 105}, {"close": 103}]})
    assert output is None or output.getbuffer().nbytes > 0
    if output is not None:
        assert output.getbuffer().nbytes <= service.max_output_bytes


@pytest.mark.asyncio
async def test_market_chart_service_rejects_unbounded_or_missing_rows():
    service = MarketChartService()
    with pytest.raises(MarketChartError, match="market_chart_rows_invalid"):
        await service.render({"symbol": "X", "rows": [{"close": 1}]})
    with pytest.raises(MarketChartError, match="market_chart_schema_error"):
        await service.render({"symbol": "X", "rows": [{"value": 1}, {"value": 2}]})
