import pytest

from src.tools.air_quality_tool import AirQualityTool
from src.tools.crypto_tool import CryptoTool
from src.tools.market_tool import MarketTool
from src.tools.spatula_tool import StoryTool


class BrokenProvider:
    async def fetch(self, request):
        raise ValueError("token=secret https://private.example/internal")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_factory", "kwargs", "fallback_code"),
    [
        (lambda: AirQualityTool(BrokenProvider()), {"city": "Ankara"}, "AIR_QUALITY_INVALID_REQUEST"),
        (lambda: CryptoTool(BrokenProvider()), {"symbol": "btc"}, "CRYPTO_INVALID_REQUEST"),
        (lambda: MarketTool(BrokenProvider()), {"symbol": "THYAO.IS"}, "MARKET_INVALID_REQUEST"),
        (lambda: StoryTool(BrokenProvider()), {"topic": "uzay"}, "STORY_INVALID_REQUEST"),
    ],
)
async def test_provider_tools_do_not_echo_untrusted_validation_text(tool_factory, kwargs, fallback_code):
    result = await tool_factory().execute(**kwargs)

    assert result["error"] in {
        "air_quality_request_rejected",
        "crypto_request_rejected",
        "market_request_rejected",
        "story_request_rejected",
    }
    assert result["error_code"] == fallback_code
    assert "secret" not in str(result)
    assert "private.example" not in str(result)
