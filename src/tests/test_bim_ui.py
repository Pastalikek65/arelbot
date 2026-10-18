from types import SimpleNamespace

from src.services.bim_ui import PAGE_SIZE, bim_page_view


def test_bim_page_view_includes_prices_and_paginates_all_products():
    result = SimpleNamespace(
        ok=True,
        data={"deals": [{"title": f"Ürün {i}", "unit": "• 1 adet", "price": f"{i},00 ₺"} for i in range(PAGE_SIZE + 2)]},
        source_url="https://www.bim.com.tr/Categories/100/aktuel-urunler.aspx",
    )
    text, markup = bim_page_view(result, owner_id=42, page=0)
    assert "Sayfa 1/2" in text
    assert "Ürün 0" in text and "0,00 ₺" in text
    assert markup is not None
    assert any("bim_page:42:1" == button.callback_data for row in markup.inline_keyboard for button in row)

    second, second_markup = bim_page_view(result, owner_id=42, page=1)
    assert "Sayfa 2/2" in second
    assert "Ürün 7" in second
    assert any("bim_page:42:0" == button.callback_data for row in second_markup.inline_keyboard for button in row)


def test_bim_page_view_rejects_failed_provider_result():
    text, markup = bim_page_view(SimpleNamespace(ok=False), owner_id=1)
    assert "alınamıyor" in text
    assert markup is None
