from __future__ import annotations

import httpx
import pytest

from src.features.contracts import ProviderRequest
from src.features.providers.public_data import (
    BimDealsProvider,
    CurrencyProvider,
    FuelProvider,
    PharmacyProvider,
    PrayerTimesProvider,
)
from src.features.providers.crypto import CryptoProvider
from src.features.providers.public_data import MAX_RESPONSE_BYTES


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_crypto_provider_normalizes_aliases_and_currency():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/simple/price")
        assert request.url.params["ids"] == "bitcoin,ethereum"
        assert request.url.params["vs_currencies"] == "try"
        return httpx.Response(
            200,
            json={
                "bitcoin": {"try": 2100000.0, "try_24h_change": 1.25, "last_updated_at": 1},
                "ethereum": {"try": 70000.0, "try_24h_change": -0.5, "last_updated_at": 1},
            },
            request=request,
        )

    async with _client(handler) as client:
        result = await CryptoProvider(client=client).fetch(
            ProviderRequest(operation="crypto", params={"symbol": "btc,eth", "vs_currency": "try"})
        )

    assert [row["id"] for row in result.data["coins"]] == ["bitcoin", "ethereum"]
    assert result.data["coins"][0]["change_24h"] == 1.25
    assert "CoinGecko" == result.source
    assert "Kripto Piyasası" in result.data["content"]


@pytest.mark.asyncio
async def test_crypto_provider_rejects_unknown_or_unsafe_input():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}, request=request)

    async with _client(handler) as client:
        provider = CryptoProvider(client=client)
        with pytest.raises(ValueError, match="crypto_symbol_invalid"):
            await provider.fetch(ProviderRequest(operation="crypto", params={"symbol": "btc;rm"}))
        with pytest.raises(ValueError, match="crypto_unknown_coin"):
            await provider.fetch(ProviderRequest(operation="crypto", params={"symbol": "not-a-real-coin"}))


@pytest.mark.asyncio
async def test_crypto_provider_uses_pro_header_only_for_pro_plan():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-cg-pro-api-key") == "secret"
        assert request.headers.get("x-cg-demo-api-key") is None
        return httpx.Response(200, json={"bitcoin": {"usd": 1}}, request=request)

    async with _client(handler) as client:
        result = await CryptoProvider(client=client, api_key="secret", api_plan="pro").fetch(
            ProviderRequest(operation="crypto", params={"symbol": "btc"})
        )
    assert result.data["coins"][0]["id"] == "bitcoin"


@pytest.mark.asyncio
async def test_pharmacy_provider_parses_and_escapes_fixture():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/istanbul/besiktas")
        return httpx.Response(
            200,
            text="""
            <div class='list-group-item'>
              <h5>Merkez <script>alert(1)</script> Eczanesi</h5>
              <p>İstiklal &amp; No: 4</p><a href='tel:+902121234567'>0212 123 45 67</a>
            </div>
            """,
            request=request,
        )

    async with _client(handler) as client:
        result = await PharmacyProvider(client=client).fetch(
            ProviderRequest(operation="pharmacy", params={"city": "İstanbul", "district": "Beşiktaş"})
        )

    assert result.data["pharmacies"][0]["name"] == "Merkez Eczanesi"
    assert "İstiklal &amp; No: 4" in result.data["content"]
    assert "<script>" not in result.data["content"]
    assert result.metadata["provider_kind"] == "typed"


@pytest.mark.asyncio
async def test_fuel_provider_filters_city_and_normalizes_rows():
    fixture = """
    <table><tr><th>Şehir</th><th>Benzin</th><th>Motorin</th><th>LPG</th></tr>
      <tr><td>İstanbul</td><td>42,10</td><td>44,20</td><td>25,00</td></tr>
      <tr><td>Ankara</td><td>41,90</td><td>44,00</td><td>24,80</td></tr>
    </table>
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=fixture, request=request)

    async with _client(handler) as client:
        result = await FuelProvider(client=client).fetch(
            ProviderRequest(operation="fuel", params={"city": "istanbul"})
        )

    assert len(result.data["prices"]) == 1
    assert result.data["prices"][0]["diesel"] == "44,20"
    assert "Ankara" not in result.data["content"]


@pytest.mark.asyncio
async def test_currency_provider_rejects_invalid_schema():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>empty</body></html>", request=request)

    async with _client(handler) as client:
        with pytest.raises(RuntimeError, match="currency_schema_error"):
            await CurrencyProvider(client=client).fetch(ProviderRequest(operation="currency"))


@pytest.mark.asyncio
async def test_currency_provider_escapes_rate_values():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""
            <div class='item'><span class='name'>DOLAR</span><span class='value'>42,10 &lt;tag&gt;</span>
              <span class='change'>%1,2</span></div>
            """,
            request=request,
        )

    async with _client(handler) as client:
        result = await CurrencyProvider(client=client).fetch(ProviderRequest(operation="currency"))

    assert result.data["rates"][0]["name"] == "DOLAR"
    assert "&lt;tag&gt;" in result.data["content"]
    assert result.data["rates"][0]["numeric_value"] == 42.1


@pytest.mark.asyncio
async def test_currency_provider_normalizes_turkish_numeric_values():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<div class='item'><span class='name'>USD</span><span class='value'>41,25 TL</span></div>",
            request=request,
        )

    async with _client(handler) as client:
        result = await CurrencyProvider(client=client).fetch(ProviderRequest(operation="currency"))

    assert result.data["rates"][0]["numeric_value"] == 41.25


@pytest.mark.asyncio
async def test_bim_provider_parses_deals():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<div class='item'><h2 class='title'>Kahve makinesi</h2><span class='quant'>999 TL</span></div>",
            request=request,
        )

    async with _client(handler) as client:
        result = await BimDealsProvider(client=client).fetch(ProviderRequest(operation="bim"))

    assert result.data["deals"] == [{"title": "Kahve makinesi", "price": "999 TL"}]
    assert "Kahve makinesi" in result.data["content"]


@pytest.mark.asyncio
async def test_prayer_provider_parses_turkish_times():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""
            <div class='vakitler-list'>
              <li><span class='vakit-label'>İmsak</span><span class='vakit-time'>05:12</span></li>
              <li><span class='vakit-label'>Akşam</span><span class='vakit-time'>19:44</span></li>
            </div>
            """,
            request=request,
        )

    async with _client(handler) as client:
        result = await PrayerTimesProvider(client=client).fetch(
            ProviderRequest(operation="prayer_times", params={"city": "İstanbul"})
        )

    assert result.data["times"] == {"İmsak": "05:12", "Akşam": "19:44"}
    assert "19:44" in result.data["content"]


@pytest.mark.asyncio
async def test_pharmacy_provider_parses_current_nearest_card_schema():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/istanbul")
        return httpx.Response(
            200,
            text="""
            <article class="nearest-pharmacy-card">
              <div class="nearest-pharmacy-card__name-row">Yeni Eczane</div>
              <div class="nearest-pharmacy-card__address">Merkez Mahallesi</div>
              <div class="nearest-pharmacy-card__phone">0212 000 00 00</div>
            </article>
            """,
            request=request,
        )

    async with _client(handler) as client:
        result = await PharmacyProvider(client=client).fetch(
            ProviderRequest(operation="pharmacy", params={"city": "İstanbul"})
        )
    assert result.data["pharmacies"][0]["name"] == "Yeni Eczane"
    assert result.data["pharmacies"][0]["address"] == "Merkez Mahallesi"


@pytest.mark.asyncio
async def test_fuel_provider_parses_distributor_table_and_istanbul_route():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/istanbul-avrupa")
        return httpx.Response(
            200,
            text="""
            <table><tr><th>Dağıtıcı</th><th>Benzin</th><th>Motorin</th><th>LPG</th></tr>
              <tr><td>Petrol Ofisi</td><td>42,10</td><td>44,20</td><td>25,00</td></tr>
            </table>
            """,
            request=request,
        )

    async with _client(handler) as client:
        result = await FuelProvider(client=client).fetch(
            ProviderRequest(operation="fuel", params={"city": "İstanbul"})
        )
    assert result.data["prices"][0]["distributor"] == "Petrol Ofisi"
    assert result.data["prices"][0]["city"] == "İstanbul"


@pytest.mark.asyncio
async def test_fuel_provider_normalizes_public_json_snapshot():
    payload = {
        "prices": [
            {"il": "İstanbul", "marka": "Arel Petrol", "yakıt": "Benzin", "fiyat": "42,10"},
            {"il": "İstanbul", "marka": "Arel Petrol", "yakıt": "Motorin", "fiyat": "44,20"},
            {"il": "İstanbul", "marka": "Arel Petrol", "yakıt": "LPG", "fiyat": "25,00"},
        ]
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "guncelakaryakitfiyatlari.com"
        return httpx.Response(200, json=payload, request=request)

    async with _client(handler) as client:
        result = await FuelProvider(client=client, prefer_json=True).fetch(
            ProviderRequest(operation="fuel", params={"city": "İstanbul"})
        )

    assert result.metadata["format"] == "json"
    assert result.data["prices"][0]["gasoline"] == "42,10"
    assert result.data["prices"][0]["diesel"] == "44,20"
    assert "EPDK" in result.data["content"] or result.source.startswith("EPDK")


@pytest.mark.asyncio
async def test_fuel_provider_normalizes_documented_province_snapshot():
    payload = {
        "provinces": [
            {
                "name": "İstanbul",
                "median": {"benzin": "52.10", "motorin": "54.20"},
                "lpg": {"price": "29.30"},
            }
        ]
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    async with _client(handler) as client:
        result = await FuelProvider(client=client, prefer_json=True).fetch(
            ProviderRequest(operation="fuel", params={"city": "İstanbul"})
        )

    row = result.data["prices"][0]
    assert row["distributor"] == "İl medyanı"
    assert row["gasoline"] == "52.10"
    assert row["diesel"] == "54.20"
    assert row["lpg"] == "29.30"


@pytest.mark.asyncio
async def test_bim_provider_parses_current_product_image_links():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                '<a href="/aktuel-urunler/kahve-makinesi/aktuel.aspx">'
                '<img src="https://cdn1.bim.com.tr/uploads/aktuel-urunler/kahve.jpg">'
                "</a>"
            ),
            request=request,
        )

    async with _client(handler) as client:
        result = await BimDealsProvider(client=client).fetch(ProviderRequest(operation="bim"))
    assert result.data["deals"][0]["title"] == "Kahve Makinesi"
    assert result.data["deals"][0]["image_url"].endswith("/kahve.jpg")


@pytest.mark.asyncio
async def test_bim_provider_reads_official_product_card_name_unit_and_price():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""
            <div class="product"><div class="inner">
              <div class="imageArea"><a href="/aktuel-urunler/kahve/aktuel.aspx">
                <img src="https://cdn1.bim.com.tr/uploads/aktuel-urunler/kahve.jpg">
              </a></div>
              <div class="descArea">
                <h2 class="subTitle">Arel</h2><h2 class="title">Kahve Makinesi</h2>
                <div class="gramajadet">• 1 adet</div>
                <div class="priceArea"><div class="quantify">1.299,</div><span class="number">90</span><span class="curr">₺</span></div>
              </div>
            </div></div>
            """,
            request=request,
        )

    async with _client(handler) as client:
        result = await BimDealsProvider(client=client).fetch(ProviderRequest(operation="bim"))

    product = result.data["deals"][0]
    assert product["title"] == "Arel Kahve Makinesi"
    assert product["unit"] == "• 1 adet"
    assert product["price"] == "1.299,90 ₺"
    assert product["url"].startswith("https://www.bim.com.tr/")


@pytest.mark.asyncio
async def test_html_provider_rejects_untrusted_host_before_network_call():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, text="<div class='item'>unexpected</div>", request=request)

    async with _client(handler) as client:
        provider = CurrencyProvider(client=client)
        with pytest.raises(RuntimeError, match="provider_host_not_allowed"):
            await provider._fetch_html(
                ProviderRequest(operation="currency"),
                "https://evil.example.invalid/rates",
            )

    assert called is False


@pytest.mark.asyncio
async def test_html_provider_rejects_non_html_content_type():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"{\"secret\":\"not a page\"}",
            headers={"content-type": "application/json"},
            request=request,
        )

    async with _client(handler) as client:
        with pytest.raises(RuntimeError, match="provider_content_type_invalid"):
            await CurrencyProvider(client=client).fetch(ProviderRequest(operation="currency"))


@pytest.mark.asyncio
async def test_html_provider_rejects_bounded_response_overflow():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * (MAX_RESPONSE_BYTES + 1),
            headers={"content-type": "text/html"},
            request=request,
        )

    async with _client(handler) as client:
        with pytest.raises(RuntimeError, match="provider_response_too_large"):
            await CurrencyProvider(client=client).fetch(ProviderRequest(operation="currency"))
