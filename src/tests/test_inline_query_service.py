from types import SimpleNamespace

import pytest

from src.features.city_directory import CityDirectory
from src.services.inline_query_service import InlineQueryService


class FakeDatabase:
    async def get_top_quotes(self, chat_id, offset, limit):
        return [
            {"sticker_file_id": "quote-1"},
            {"sticker_file_id": "quote-2"},
        ][offset:offset + limit]

    async def search_stickers(self, query, *, offset, limit):
        assert query == "kedi"
        rows = [{"file_id": f"sticker-{index}"} for index in range(3)]
        return rows[offset:offset + limit]


class FakeFeatureExecutor:
    async def execute(self, key, params):
        assert key == "news_digest"
        return SimpleNamespace(
            ok=True,
            data={"items": [
                {"title": "İstanbul ulaşım duyurusu", "link": "https://example.com/news", "description": "Kısa açıklama"},
                {"title": "Ankara hava raporu", "link": "javascript:bad", "description": "Güvenli olmayan bağlantı"},
            ]},
        )


class FakeFinanceExecutor:
    async def execute(self, key, params):
        if key == "crypto":
            assert params == {"symbol": "btc", "vs_currency": "usd"}
            return SimpleNamespace(
                ok=True,
                data={
                    "coins": [{"id": "bitcoin", "price": 100.0, "change_24h": 2.5, "currency": "usd"}]
                },
                source_url="https://www.coingecko.com/en/api",
            )
        assert key == "market"
        assert params == {"symbol": "thy"}
        return SimpleNamespace(
            ok=True,
            data={"symbol": "THYAO.IS", "price": 300.0, "change_pct": -1.25},
            source_url="https://finance.yahoo.com/quote/THYAO.IS",
        )


class FakeWebExecutor:
    async def execute(self, key, params):
        if key == "web_search":
            assert params["query"] == "yapay zeka"
            return SimpleNamespace(
                ok=True,
                data={
                    "results": [
                        {"title": "Güvenilir sonuç", "content": "Kısa özet", "url": "https://example.com/result"},
                        {"title": "Özel ağ sonucu", "content": "Engellenmeli", "url": "http://127.0.0.1/private"},
                    ]
                },
            )
        assert key == "lyrics"
        assert params == {"song": "Bir Şarkı", "artist": "Sanatçı"}
        return SimpleNamespace(
            ok=True,
            data={"title": "Bir Şarkı - Sanatçı", "excerpt": "Kısa alıntı"},
            source_url="https://genius.com/song",
        )


class FakeCultureExecutor:
    async def execute(self, key, params):
        assert key == "culture_recommendation"
        assert params == {
            "category": "book",
            "specific_query": "fantastik",
            "genre": "fantastik",
        }
        return SimpleNamespace(
            ok=True,
            data={
                "items": [
                    {
                        "title": "Güvenli kitap",
                        "author": "Yazar",
                        "year": 2024,
                        "url": "https://openlibrary.org/works/OL1W",
                    },
                    {
                        "title": "Kimlik sızdırmayan sonuç",
                        "url": "https://user:secret@example.com/item",
                    },
                ]
            },
        )


@pytest.mark.asyncio
async def test_inline_city_search_supports_turkish_aliases_and_bounded_pagination():
    service = InlineQueryService(CityDirectory(), FakeDatabase())
    page = await service.search("ŞEHİR İstanbul", "0")
    assert page.articles[0].title.startswith("İstanbul")
    assert page.cache_time == 3600
    assert service.parse_offset("-100") == 0
    assert service.parse_offset("not-a-number") == 0
    assert service.normalize_query("  şehir   İstanbul  ") == "şehir İstanbul"


@pytest.mark.asyncio
async def test_inline_city_broad_query_keeps_later_pages_complete():
    service = InlineQueryService(CityDirectory(), FakeDatabase())
    first = await service.search("sehir i", "0")
    later = await service.search("sehir i", "20")
    assert first.articles
    assert later.articles
    assert first.next_offset == "20"
    assert not {item.result_id for item in first.articles}.intersection(item.result_id for item in later.articles)


@pytest.mark.asyncio
async def test_inline_quote_and_sticker_queries_are_paginated_and_typed():
    service = InlineQueryService(CityDirectory(), FakeDatabase())
    quotes = await service.search("top:-100", "0")
    stickers = await service.search("sticker kedi", "0")
    assert [item.file_id for item in quotes.stickers] == ["quote-1", "quote-2"]
    assert [item.file_id for item in stickers.stickers] == ["sticker-0", "sticker-1", "sticker-2"]
    assert all(len(item.result_id) <= 64 for item in stickers.stickers)
    assert quotes.next_offset == ""
    assert stickers.next_offset == ""


@pytest.mark.asyncio
async def test_inline_news_uses_typed_provider_and_filters_unsafe_links():
    service = InlineQueryService(CityDirectory(), FakeDatabase(), FakeFeatureExecutor())
    page = await service.search("haber istanbul", "0")
    assert len(page.articles) == 1
    assert page.articles[0].title.startswith("İstanbul")
    assert page.articles[0].text.endswith("https://example.com/news")
    assert page.cache_time == 120


@pytest.mark.asyncio
async def test_inline_finance_uses_typed_crypto_and_market_runtime():
    service = InlineQueryService(CityDirectory(), FakeDatabase(), FakeFinanceExecutor())
    crypto = await service.search("kripto btc", "0")
    market = await service.search("borsa thy", "0")
    assert crypto.articles[0].title.startswith("bitcoin")
    assert "100" in crypto.articles[0].text
    assert crypto.cache_time == 30
    assert market.articles[0].title.startswith("THYAO.IS")
    assert "300" in market.articles[0].text
    assert market.cache_time == 120


@pytest.mark.asyncio
async def test_inline_unknown_query_returns_safe_empty_page():
    service = InlineQueryService(CityDirectory(), FakeDatabase())
    page = await service.search("something private", "999999")
    assert page.articles == ()
    assert page.stickers == ()
    assert page.cache_time == 15


@pytest.mark.asyncio
async def test_inline_web_search_is_typed_bounded_and_filters_private_links():
    service = InlineQueryService(CityDirectory(), FakeDatabase(), FakeWebExecutor())
    page = await service.search("ara yapay zeka", "0")
    assert len(page.articles) == 1
    assert page.articles[0].title == "Güvenilir sonuç"
    assert page.articles[0].text.endswith("https://example.com/result")
    assert page.cache_time == 60


@pytest.mark.asyncio
async def test_inline_lyrics_returns_bounded_excerpt_with_source():
    service = InlineQueryService(CityDirectory(), FakeDatabase(), FakeWebExecutor())
    page = await service.search("soz Bir Şarkı - Sanatçı", "0")
    assert len(page.articles) == 1
    assert "Kısa alıntı" in page.articles[0].text
    assert page.articles[0].text.endswith("Tam şarkı sözleri yeniden üretilmez.")
    assert page.cache_time == 300


@pytest.mark.asyncio
async def test_inline_culture_without_category_returns_bounded_category_hints():
    service = InlineQueryService(CityDirectory(), FakeDatabase(), FakeCultureExecutor())
    page = await service.search("kultur", "0")
    assert [article.title for article in page.articles] == ["🎬 Film", "📺 Dizi", "📚 Kitap", "🎵 Müzik"]
    assert page.cache_time == 30


@pytest.mark.asyncio
async def test_inline_culture_calls_typed_provider_and_filters_unsafe_item_links():
    service = InlineQueryService(CityDirectory(), FakeDatabase(), FakeCultureExecutor())
    page = await service.search("kültür kitap fantastik", "0")
    assert len(page.articles) == 2
    assert page.articles[0].title == "Güvenli kitap"
    assert "Kaynak: https://openlibrary.org/works/OL1W" in page.articles[0].text
    assert "secret" not in page.articles[1].text
    assert page.cache_time == 900


@pytest.mark.asyncio
async def test_inline_culture_rejects_unknown_category_without_provider_call():
    class Executor:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("unknown category must not call provider")

    service = InlineQueryService(CityDirectory(), FakeDatabase(), Executor())
    page = await service.search("kultur podcast truecrime", "0")
    assert page.articles == ()
    assert page.cache_time == 15
