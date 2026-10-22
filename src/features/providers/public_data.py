"""Typed public-data providers for the Turkey-focused feature set.

These providers deliberately keep source-specific parsing behind the common
``DataProvider`` contract. Telegram handlers and AI tools receive the same
structured response envelope, while source failures are surfaced to the
runtime so cache, circuit-breaker and stale-data policies remain consistent.
"""

from __future__ import annotations

import html
import re
import unicodedata
from abc import ABC, abstractmethod
from typing import Any, Iterable
from urllib.parse import quote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse
from .transport import ProviderHttpTransport, ProviderTransportError
from src.security.tls import httpx_verify_kwargs


DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
    # The user agent identifies the application only.  A URL would name a host
    # that no license record can govern, because the project has no public
    # contact domain.
    "User-Agent": "Arelbot/1.0",
}

# Public HTML pages are untrusted input. Keep the parser bounded even when an
# upstream accidentally returns a binary dump or a very large archive.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})


def _text(node: Any) -> str:
    """Collapse HTML whitespace and return text safe for later escaping."""

    if node is None:
        return ""
    if hasattr(node, "get_text"):
        value = node.get_text(" ", strip=True)
    else:
        value = str(node)
    return " ".join(value.split())


def _numeric_amount(value: str) -> float | None:
    """Parse Turkish formatted amounts without trusting surrounding markup."""
    raw = re.sub(r"[^0-9,.-]", "", str(value or "")).strip()
    if not raw:
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif raw.count(",") == 1:
        raw = raw.replace(",", ".")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _slug(value: str) -> str:
    """Create a deterministic Turkish URL slug without trusting user input."""

    replacements = str.maketrans(
        {
            "ç": "c",
            "Ç": "c",
            "ğ": "g",
            "Ğ": "g",
            "ı": "i",
            "İ": "i",
            "ö": "o",
            "Ö": "o",
            "ş": "s",
            "Ş": "s",
            "ü": "u",
            "Ü": "u",
        }
    )
    normalized = unicodedata.normalize("NFKD", str(value or "").translate(replacements))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    if not ascii_value:
        raise ValueError("value_required")
    return quote(ascii_value[:80], safe="-")


class _HtmlProvider(ABC):
    """Shared HTTP lifecycle and health behavior for HTML public sources."""

    capabilities: set[str] = set()
    timeout_seconds = 8.0
    source_name = "Public data source"
    source_url: str | None = None

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds, connect=3.0),
                headers=DEFAULT_HEADERS,
                follow_redirects=False,
                **httpx_verify_kwargs(),
            )
        return self._owned_client

    async def _fetch_html(self, request: ProviderRequest, url: str) -> BeautifulSoup:
        parsed = urlsplit(str(url))
        expected_url = getattr(self, "source_url", None) or getattr(self, "source_base_url", None)
        expected = urlsplit(str(expected_url or ""))
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or expected.hostname is None
            or parsed.hostname.casefold().rstrip(".") != expected.hostname.casefold().rstrip(".")
        ):
            raise RuntimeError("provider_host_not_allowed")
        client = await self._get_client()
        timeout = min(max(float(request.timeout_seconds), 0.5), self.timeout_seconds)
        try:
            payload = await ProviderHttpTransport(
                client,
                allowed_hosts={expected.hostname},
                max_response_bytes=MAX_RESPONSE_BYTES,
            ).get_bytes(
                url,
                timeout=timeout,
                expected_content_types=_HTML_CONTENT_TYPES,
            )
        except ProviderTransportError as exc:
            raise exc
        if not payload.body.strip():
            raise RuntimeError(f"{self.name}_empty_response")
        return BeautifulSoup(payload.body, "html.parser")

    @abstractmethod
    async def fetch(self, request: ProviderRequest) -> ProviderResponse[Any]:
        raise NotImplementedError

    async def normalize(self, raw: Any) -> Any:
        return raw

    async def health_check(self) -> ProviderHealth:
        # Health is intentionally lazy; a /health request must not amplify
        # traffic against public pages or turn an upstream outage into a bot
        # outage. Runtime calls provide the real latency/error signal.
        return ProviderHealth(name=self.name, healthy=True, detail="lazy_probe")

    async def close(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None


class PharmacyProvider(_HtmlProvider):
    name = "get_pharmacies"
    capabilities = {"pharmacy", "on_duty_pharmacy"}
    source_name = "Nöbetçi Eczaneler"
    source_base_url = "https://www.nobetcieczaneler.org/nobetci-eczane"

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        city = str(request.params.get("city") or "").strip()
        district = str(request.params.get("district") or "").strip() or None
        city_slug = _slug(city)
        path = f"{self.source_base_url}/{city_slug}"
        if district:
            path = f"{path}/{_slug(district)}"
        soup = await self._fetch_html(request, path)
        cards = soup.select("article.nearest-pharmacy-card")
        if not cards:
            cards = soup.select(".list-group-item") or soup.select("div.col-md-6")
        rows: list[dict[str, str]] = []
        for card in cards[:12]:
            name = _text(card.select_one("h5, strong, .name, .nearest-pharmacy-card__name-row"))
            if not name:
                continue
            address = _text(card.select_one("p, .address, .nearest-pharmacy-card__address")) or "Adres bilgisi yok"
            phone = _text(card.select_one('a[href^="tel"], .phone, .nearest-pharmacy-card__phone')) or "Telefon bilgisi yok"
            rows.append({"name": name, "address": address, "phone": phone})
        if not rows:
            raise RuntimeError("pharmacy_schema_error")
        scope = f" {html.escape(district)}" if district else ""
        lines = [f"<b>🏥 {html.escape(city)}{scope} Nöbetçi Eczaneler</b>", ""]
        for row in rows:
            lines.append(
                f"💊 <b>{html.escape(row['name'])}</b>\n"
                f"📍 {html.escape(row['address'])}\n"
                f"📞 {html.escape(row['phone'])}"
            )
        return ProviderResponse(
            data={"city": city, "district": district, "pharmacies": rows, "content": "\n\n".join(lines)},
            source=self.source_name,
            source_url=path,
            metadata={"provider_kind": "typed", "records": len(rows)},
        )


class FuelProvider(_HtmlProvider):
    name = "get_fuel_prices"
    capabilities = {"fuel", "fuel_prices"}
    source_name = "EPDK akaryakıt verisi"
    source_base_url = "https://www.doviz.com/akaryakit-fiyatlari"
    source_url = source_base_url
    json_url = "https://guncelakaryakitfiyatlari.com/veri/fiyatlar.json"

    def __init__(self, client: httpx.AsyncClient | None = None, *, prefer_json: bool | None = None) -> None:
        super().__init__(client=client)
        # Existing replay fixtures exercise the HTML compatibility path. Live
        # deployments use the bounded JSON snapshot first, then fall back to
        # the legacy page when the snapshot is temporarily unavailable.
        self._prefer_json = (client is None) if prefer_json is None else bool(prefer_json)

    @staticmethod
    def _json_value(item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if value is not None and str(value).strip():
                return _text(value)
        return ""

    @classmethod
    def _json_rows(cls, payload: Any, city_filter: str) -> list[dict[str, Any]]:
        # The documented snapshot groups data by province and then exposes a
        # median plus distributor rows.  Prefer the median because it is
        # stable, compact, and avoids presenting one distributor as the whole
        # city's price.
        if isinstance(payload, dict) and isinstance(payload.get("provinces"), list):
            normalized_filter = _slug(city_filter) if city_filter else ""
            rows: list[dict[str, Any]] = []
            for province in payload["provinces"]:
                if not isinstance(province, dict):
                    continue
                city = cls._json_value(province, "name", "city", "il")
                if normalized_filter and normalized_filter not in _slug(city):
                    continue
                median = province.get("median")
                if not isinstance(median, dict):
                    continue
                gasoline = cls._json_value(median, "benzin", "gasoline")
                diesel = cls._json_value(median, "motorin", "diesel", "mazot")
                lpg_data = province.get("lpg")
                lpg = cls._json_value(lpg_data, "price", "fiyat", "lpg", "otogaz") if isinstance(lpg_data, dict) else _text(lpg_data)
                if gasoline or diesel or lpg:
                    rows.append(
                        {
                            "distributor": "İl medyanı",
                            "city": city or city_filter or None,
                            "gasoline": gasoline,
                            "diesel": diesel,
                            "lpg": lpg,
                            "gasoline_value": _numeric_amount(gasoline),
                            "diesel_value": _numeric_amount(diesel),
                            "lpg_value": _numeric_amount(lpg),
                        }
                    )
                if len(rows) >= 10:
                    break
            return rows
        if isinstance(payload, list):
            candidates = payload
        elif isinstance(payload, dict):
            candidates = []
            for key in ("prices", "fiyatlar", "items", "data", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    candidates = value
                    break
            if not candidates and any(key in payload for key in ("city", "il", "şehir", "sehir", "marka", "brand")):
                candidates = [payload]
        else:
            candidates = []

        normalized_filter = _slug(city_filter) if city_filter else ""
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for item in candidates:
            if not isinstance(item, dict):
                continue
            city = cls._json_value(item, "city", "il", "şehir", "sehir", "province")
            if normalized_filter and city and normalized_filter not in _slug(city):
                continue
            distributor = cls._json_value(item, "distributor", "dağıtıcı", "dagitici", "marka", "brand", "firma") or "Ortalama"
            key = (city or city_filter or "Türkiye", distributor)
            row = grouped.setdefault(
                key,
                {
                    "distributor": distributor,
                    "city": city or city_filter or None,
                    "gasoline": "",
                    "diesel": "",
                    "lpg": "",
                    "gasoline_value": None,
                    "diesel_value": None,
                    "lpg_value": None,
                },
            )
            # Some snapshots provide one row with three fuel columns.
            for output, keys in {
                "gasoline": ("gasoline", "benzin", "benzin_95", "kurşunsuz_95", "kursunsuz95"),
                "diesel": ("diesel", "motorin", "mazot"),
                "lpg": ("lpg", "otogaz"),
            }.items():
                value = cls._json_value(item, *keys)
                if value:
                    row[output] = value
                    row[f"{output}_value"] = _numeric_amount(value)

            # Or the source has one fuel type and price per row.
            fuel_type = _slug(cls._json_value(item, "fuel_type", "fuel", "yakıt", "yakit", "type"))
            price = cls._json_value(item, "price", "fiyat", "value", "tutar")
            if price:
                output = "gasoline" if any(token in fuel_type for token in ("benzin", "gasoline", "95")) else (
                    "diesel" if any(token in fuel_type for token in ("motorin", "diesel", "mazot")) else (
                        "lpg" if any(token in fuel_type for token in ("lpg", "otogaz")) else None
                    )
                )
                if output:
                    row[output] = price
                    row[f"{output}_value"] = _numeric_amount(price)
        return [row for row in grouped.values() if row["gasoline"] or row["diesel"] or row["lpg"]]

    @staticmethod
    def _render_rows(rows: list[dict[str, Any]], city_filter: str) -> str:
        lines = ["<b>⛽ Güncel Akaryakıt Fiyatları</b>", ""]
        for row in rows[:10]:
            details = f"Benzin: {html.escape(row['gasoline'])} TL · Motorin: {html.escape(row['diesel'])} TL"
            if row["lpg"]:
                details += f" · LPG: {html.escape(row['lpg'])} TL"
            scope = html.escape(city_filter or row.get("city") or "Türkiye")
            lines.append(f"📍 <b>{html.escape(row['distributor'])}</b> ({scope})\n{details}")
        return "\n\n".join(lines)

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        city_filter = str(request.params.get("city") or "").strip()
        if self._prefer_json:
            try:
                client = await self._get_client()
                payload = await ProviderHttpTransport(
                    client,
                    allowed_hosts={"guncelakaryakitfiyatlari.com"},
                ).get_json(
                    self.json_url,
                    timeout=min(max(float(request.timeout_seconds), 0.5), self.timeout_seconds),
                    error_code="fuel_json_invalid",
                )
                rows = self._json_rows(payload, city_filter)
                if rows:
                    return ProviderResponse(
                        data={"city": city_filter or None, "prices": rows, "content": self._render_rows(rows, city_filter)},
                        source="EPDK akaryakıt verisi",
                        source_url=self.json_url,
                        metadata={"provider_kind": "typed", "records": len(rows), "format": "json", "attribution": "EPDK; derleyen Güncel Akaryakıt Fiyatları"},
                    )
            except (ProviderTransportError, RuntimeError, ValueError):
                # Fall through to the bounded HTML compatibility source.
                pass
        normalized_filter = _slug(city_filter) if city_filter else ""
        path_slug = "istanbul-avrupa" if normalized_filter == "istanbul" else normalized_filter
        source_url = f"{self.source_base_url}/{path_slug}" if path_slug else self.source_base_url
        soup = await self._fetch_html(request, source_url)
        table = soup.find("table")
        header_cells = table.find("tr").find_all(["th", "td"]) if table and table.find("tr") else []
        header_name = _slug(_text(header_cells[0])) if header_cells else ""
        city_table = header_name in {"sehir", "city", "il"}
        rows: list[dict[str, str]] = []
        for row in table.find_all("tr") if table else []:
            cells = [_text(cell) for cell in row.find_all("td")]
            if len(cells) < 4 or not cells[0]:
                continue
            if city_table and normalized_filter and normalized_filter not in _slug(cells[0]):
                continue
            rows.append(
                {
                    "distributor": cells[0],
                    "city": city_filter or None,
                    "gasoline": cells[1],
                    "diesel": cells[2],
                    "lpg": cells[3],
                    "gasoline_value": _numeric_amount(cells[1]),
                    "diesel_value": _numeric_amount(cells[2]),
                    "lpg_value": _numeric_amount(cells[3]),
                }
            )
        if not rows:
            raise RuntimeError("fuel_schema_error")
        lines = ["<b>⛽ Güncel Akaryakıt Fiyatları</b>", ""]
        for row in rows[:10]:
            details = f"Benzin: {html.escape(row['gasoline'])} TL · Motorin: {html.escape(row['diesel'])} TL"
            if row["lpg"]:
                details += f" · LPG: {html.escape(row['lpg'])} TL"
            scope = html.escape(city_filter) if city_filter else "T\u00fcrkiye"
            lines.append(f"\U0001f4cd <b>{html.escape(row['distributor'])}</b> ({scope})\\n{details}")
        return ProviderResponse(
            data={"city": city_filter or None, "prices": rows, "content": "\n\n".join(lines)},
            source="Döviz.com akaryakıt fiyatları",
            source_url=source_url,
            metadata={"provider_kind": "typed", "records": len(rows), "format": "html", "attribution": "Döviz.com"},
        )


class CurrencyProvider(_HtmlProvider):
    name = "get_currency_rates"
    capabilities = {"currency", "gold", "exchange_rates"}
    source_name = "Döviz.com"
    source_url = "https://www.doviz.com/"

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        soup = await self._fetch_html(request, self.source_url)
        items = soup.select(".item")
        rates: list[dict[str, str]] = []
        for item in items[:30]:
            name = _text(item.select_one(".name, .item-name"))
            value = _text(item.select_one(".value, .item-value"))
            change = _text(item.select_one(".change, .item-change"))
            if name and value:
                rates.append({
                    "name": name,
                    "value": value,
                    "numeric_value": _numeric_amount(value),
                    "change": change,
                })
        if not rates:
            raise RuntimeError("currency_schema_error")
        lines = ["<b>💰 Güncel Döviz ve Altın Kurları</b>", ""]
        for rate in rates[:12]:
            suffix = f" · {html.escape(rate['change'])}" if rate["change"] else ""
            lines.append(f"• <b>{html.escape(rate['name'])}</b>: {html.escape(rate['value'])}{suffix}")
        return ProviderResponse(
            data={"rates": rates, "content": "\n".join(lines)},
            source=self.source_name,
            source_url=self.source_url,
            metadata={"provider_kind": "typed", "records": len(rates)},
        )


class BimDealsProvider(_HtmlProvider):
    name = "get_bim_deals"
    capabilities = {"bim", "deals", "retail"}
    source_name = "BİM aktüel ürünler"
    source_url = "https://www.bim.com.tr/Categories/100/aktuel-urunler.aspx"

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        soup = await self._fetch_html(request, self.source_url)
        deals: list[dict[str, str]] = []
        # The official page renders one ``.product`` card per item. Reading the
        # card preserves the product name, size and Turkish-formatted price.
        for card in soup.select(".product"):
            image = card.select_one('img[src*="/uploads/aktuel-urunler/"]')
            if image is None:
                continue
            heading = " ".join(part for part in (
                _text(card.select_one(".subTitle")),
                _text(card.select_one(".title")),
            ) if part)
            title = re.sub(r"\s+", " ", heading).strip()
            if not title:
                continue
            unit = _text(card.select_one(".gramajadet, .quantity, .unit"))
            whole = _text(card.select_one(".priceArea .quantify, .price .quantify"))
            fraction = _text(card.select_one(".priceArea .number, .price .number"))
            currency = _text(card.select_one(".priceArea .curr, .price .curr")) or "₺"
            price = ""
            if whole:
                whole = whole.rstrip(",.")
                price = f"{whole},{fraction.zfill(2) if fraction else '00'} {currency}"
            else:
                price = _text(card.select_one(".price, .priceArea"))
            anchor = image.find_parent("a")
            href = str(anchor.get("href") or "").strip() if anchor else ""
            image_url = str(image.get("src") or "").strip()
            deals.append({
                "title": title[:160],
                "unit": unit[:80],
                "price": price[:64] or "Fiyat belirtilmemiş",
                "image_url": image_url,
                "url": urljoin(self.source_url, href) if href else self.source_url,
            })
        # Older snapshots/replay fixtures use only linked images.
        if not deals:
            for image in soup.select('a[href*="/aktuel-urunler/"] img[src*="/uploads/aktuel-urunler/"]'):
                anchor = image.find_parent("a")
                href = str(anchor.get("href") or "").strip() if anchor else ""
                parts = [part for part in href.strip("/").split("/") if part]
                raw_title = parts[1] if len(parts) > 1 else "Aktüel ürün"
                title = re.sub(r"[-_]+", " ", raw_title).strip().title() or "Aktüel ürün"
                deals.append({
                    "title": title[:120],
                    "image_url": str(image.get("src") or "").strip(),
                    "url": urljoin(self.source_url, href),
                })
        if not deals:
            for item in soup.select("div.item, article.item"):
                title = _text(item.select_one("h2.title, h2, .title"))
                if not title:
                    continue
                price = _text(item.select_one("span.quant, .price")) or "Fiyat belirtilmemiş"
                deals.append({"title": title, "price": price})
        if not deals:
            raise RuntimeError("bim_schema_error")
        lines = ["<b>🛒 BİM Aktüel Ürünler</b>", ""]
        lines.extend(
            f"🎁 <b>{html.escape(item['title'])}</b>"
            + (f" · {html.escape(item['unit'])}" if item.get("unit") else "")
            + (f" — <b>{html.escape(item['price'])}</b>" if item.get("price") else "")
            for item in deals[:10]
        )
        return ProviderResponse(
            data={"deals": deals, "content": "\n".join(lines)},
            source=self.source_name,
            source_url=self.source_url,
            metadata={"provider_kind": "typed", "records": len(deals)},
        )

class PrayerTimesProvider(_HtmlProvider):
    name = "get_prayer_times"
    capabilities = {"prayer_times", "religious_calendar"}
    source_name = "Habertürk namaz vakitleri"
    source_base_url = "https://www.haberturk.com/namaz-vakitleri"

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        city = str(request.params.get("city") or "").strip() or "İstanbul"
        url = f"{self.source_base_url}/{_slug(city)}"
        soup = await self._fetch_html(request, url)
        times: dict[str, str] = {}
        for row in soup.select("div.vakitler-list li, .prayer-times li"):
            label = _text(row.select_one(".vakit-label, .name, strong"))
            value = _text(row.select_one(".vakit-time, .time, time"))
            if label and re.fullmatch(r"\d{1,2}:\d{2}", value):
                times[label] = value
        if not times:
            text = _text(soup)
            for label in ("İmsak", "Güneş", "Öğle", "İkindi", "Akşam", "Yatsı"):
                match = re.search(rf"{re.escape(label)}\s*[:\-]?\s*(\d{{2}}:\d{{2}})", text, re.IGNORECASE)
                if match:
                    times[label] = match.group(1)
        if not times:
            raise RuntimeError("prayer_schema_error")
        lines = [f"<b>🕌 {html.escape(city)} Namaz Vakitleri</b>", ""]
        lines.extend(f"• {html.escape(label)}: <b>{html.escape(value)}</b>" for label, value in times.items())
        return ProviderResponse(
            data={"city": city, "times": times, "content": "\n".join(lines)},
            source=self.source_name,
            source_url=url,
            metadata={"provider_kind": "typed", "records": len(times)},
        )
