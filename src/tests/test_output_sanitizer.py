from src.security.output_sanitizer import render_model_html, render_model_plain_text


def test_model_markdown_emphasis_is_rendered_as_telegram_html():
    assert render_model_html("**KalÄ±n** __vurgu__") == "<b>KalÄ±n</b> <b>vurgu</b>"


def test_model_code_and_links_stay_bounded_and_safe():
    rendered = render_model_html("`<b>literal</b>` [Kaynak](https://example.com/path)")
    assert rendered == '<code>&lt;b&gt;literal&lt;/b&gt;</code> <a href="https://example.com/path">Kaynak</a>'
    unsafe = render_model_html("[kÃ¶tÃ¼](javascript:alert(1))")
    assert "<a " not in unsafe


def test_plain_fallback_does_not_expose_markdown_delimiters():
    assert render_model_plain_text("**KalÄ±n** ve `kod`") == "KalÄ±n ve kod"
