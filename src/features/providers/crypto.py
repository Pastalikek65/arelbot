"""CoinGecko-backed cryptocurrency price provider."""

from __future__ import annotations

import html
import re
from typing import Any

import httpx

from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse
from .transport import ProviderHttpTransport, ProviderTransportError
from src.security.tls import httpx_verify_kwargs


_COIN_ALIASES = {
    "btc": "bitcoin", "xbt": "bitcoin", "eth": "ethereum", "sol": "solana",
    "xrp": "ripple", "doge": "dogecoin", "ada": "cardano", "avax": "avalanche-2",
    "dot": "polkadot", "link": "chainlink", "matic": "matic-network", "pol": "polygon-ecosystem-token",
    "trx": "tron", "ton": "the-open-network", "shib": "shiba-inu",
}
_SUPPORTED_VS = {"usd", "try", "eur", "gbp"}


def _coin_ids(value: object) -> list[str]:
    raw = str(value or "btc").strip().lower()
    if raw.endswith("-usd") or raw.endswith("/usd"):
        raw = raw[:-4]
    parts = [part.strip() for part in re.split(r"[,\s]+", raw) if part.strip()]
    if not parts or len(parts) > 10:
        raise ValueError("crypto_symbol_invalid")
    result: list[str] = []
    for part in parts:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", part):
            raise ValueError("crypto_symbol_invalid")
        result.append(_COIN_ALIASES.get(part, part))
    return list(dict.fromkeys(result))


def _format_number(value: float, currency: str) -> str:
    if abs(value) >= 1000:
        text = f"{value:,.2f}"
    elif abs(value) >= 1:
        text = f"{value:,.4f}".rstrip("0").rstrip(".")
    else:
        text = f"{value:.8f}".rstrip("0").rstrip(".")
    return f"{text} {currency.upper()}"


class CryptoProvider:
    """Fetch current prices from CoinGecko's simple price endpoint."""

    name = "get_crypto_prices"
    capabilities = {"crypto", "crypto_prices", "market_data"}

    def __init__(self, client: httpx.AsyncClient | None = None, *, endpoint: str = "https://api.coingecko.com/api/v3", api_key: str | None = None, api_plan: str = "demo") -> None:
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.api_plan = api_plan.strip().lower()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, connect=2.0),
                headers={"Accept": "application/json", "User-Agent": "Arelbot/1.0"},
                follow_redirects=False,
                **httpx_verify_kwargs(),
            )
        return self._owned_client

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        ids = _coin_ids(request.params.get("symbol") or request.params.get("coin"))
        vs_currency = str(request.params.get("vs_currency") or request.params.get("currency") or "usd").lower().strip()
        if vs_currency not in _SUPPORTED_VS:
            raise ValueError("crypto_currency_invalid")
        url = f"{self.endpoint}/simple/price"
        params = {
            "ids": ",".join(ids),
            "vs_currencies": vs_currency,
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
        }
        header_name = "x-cg-pro-api-key" if self.api_plan == "pro" else "x-cg-demo-api-key"
        headers = {header_name: self.api_key} if self.api_key else None
        try:
            payload = await ProviderHttpTransport(
                await self._get_client(),
                allowed_hosts={httpx.URL(self.endpoint).host or ""},
            ).get_json(
                url,
                params=params,
                headers=headers,
                timeout=min(max(float(request.timeout_seconds), 0.5), 5.0),
                error_code="crypto_invalid_json",
            )
        except ProviderTransportError as exc:
            raise exc
        if not isinstance(payload, dict):
            raise RuntimeError("crypto_schema_error")
        rows: list[dict[str, Any]] = []
        for coin_id in ids:
            item = payload.get(coin_id)
            if not isinstance(item, dict) or not isinstance(item.get(vs_currency), (int, float)):
                continue
            change = item.get(f"{vs_currency}_24h_change")
            rows.append({
                "id": coin_id,
                "currency": vs_currency,
                "price": float(item[vs_currency]),
                "change_24h": float(change) if isinstance(change, (int, float)) else None,
                "last_updated_at": item.get("last_updated_at"),
                "market_cap": item.get(f"{vs_currency}_market_cap"),
                "volume_24h": item.get(f"{vs_currency}_24h_vol"),
            })
        if not rows:
            raise ValueError("crypto_unknown_coin")
        lines = ["<b>â‚¿ Kripto PiyasasÄ±</b>", ""]
        for row in rows:
            change = row.get("change_24h")
            trend = f"{change:+.2f}%" if isinstance(change, float) else "â€”"
            lines.append(
                f"â€¢ <b>{html.escape(str(row['id']))}</b>: "
                f"<code>{html.escape(_format_number(row['price'], vs_currency))}</code> "
                f"<i>(24s {html.escape(trend)})</i>"
            )
        lines.extend(["", "<i>YatÄ±rÄ±m tavsiyesi deÄŸildir; piyasa da bunu yeterince hatÄ±rlatÄ±yor.</i>"])
        return ProviderResponse(
            data={"coins": rows, "currency": vs_currency, "content": "\n".join(lines)},
            source="CoinGecko",
            source_url="https://www.coingecko.com/en/api",
            metadata={"provider_kind": "typed", "records": len(rows), "requested_ids": ids, "currency": vs_currency},
        )

    async def normalize(self, raw: Any) -> Any:
        return raw

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, healthy=True, detail="lazy_probe")

    async def close(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None
