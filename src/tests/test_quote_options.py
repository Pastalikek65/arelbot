from __future__ import annotations

import pytest

from src.quotes.options import (
    QuoteOptionsError,
    normalize_quote_options,
    normalize_template_name,
)
from src.quotes.render_service import QuoteRenderError, QuoteRenderService


def test_quote_options_are_normalized_to_bounded_renderer_schema():
    options = normalize_quote_options(
        {
            "theme": " TELEGRAM ",
            "bg_color": "17212b",
            "scale": "1.25",
            "rate_enabled": "off",
            "unknown_provider_kwarg": "ignored",
        }
    )

    assert options["theme"] == "telegram"
    assert options["bg_color"] == "#17212B"
    assert options["scale"] == 1.25
    assert options["rate_enabled"] is False
    assert "unknown_provider_kwarg" not in options


@pytest.mark.parametrize(
    "value, error",
    [
        ({"theme": "unknown"}, "theme_invalid"),
        ({"scale": 0.1}, "scale_invalid"),
        ({"scale": float("inf")}, "scale_invalid"),
        ({"bg_color": "javascript"}, "bg_color_invalid"),
        ({"emoji": "\n"}, "emoji_invalid"),
        ({"rate_enabled": "maybe"}, "boolean_option_invalid"),
    ],
)
def test_quote_options_reject_unsafe_or_unbounded_values(value, error):
    with pytest.raises(QuoteOptionsError, match=error):
        normalize_quote_options(value)


@pytest.mark.parametrize("value", ["", "bad name", "<script>", "a" * 33])
def test_template_name_is_bounded_and_html_safe(value):
    with pytest.raises(QuoteOptionsError, match="template_name_invalid"):
        normalize_template_name(value)

    assert normalize_template_name("  Günlük_Not  ") == "günlük_not"


@pytest.mark.asyncio
async def test_quote_render_service_rejects_invalid_options_before_creating_artifacts(tmp_path):
    service = QuoteRenderService(output_dir=tmp_path)

    with pytest.raises(QuoteRenderError, match="scale_invalid"):
        await service.render([{"text": "Merhaba"}], {"scale": 9})

    assert not list(tmp_path.iterdir())

