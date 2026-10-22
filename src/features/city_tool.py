"""AI and command friendly city directory tool."""

from __future__ import annotations

from typing import Any

from src.tools.base import BaseTool

from .city_directory import CityDirectory


class CityDirectoryTool(BaseTool):
    def __init__(self, directory: CityDirectory | None = None):
        super().__init__(
            name="turkiye_city_lookup",
            description=(
                "Türkiye il, plaka, telefon alan kodu veya ilçe adına göre şehir bilgisi bulur. "
                "Sonuçlar yerel sürümlü veri setinden gelir."
            ),
        )
        self.directory = directory or CityDirectory()

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "İl, plaka, telefon kodu veya ilçe adı"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 8},
            },
            "required": ["query"],
        }

    async def execute(self, query: str, limit: int = 8, **_: Any) -> dict[str, Any]:
        results = self.directory.resolve(query)
        if not results:
            results = self.directory.search(query, limit=max(1, min(limit, 8)))
        if not results:
            return {"error": "CITY_NOT_FOUND", "result": "Şehir veya ilçe bulunamadı."}
        limited = results[: max(1, min(limit, 8))]
        return {
            "result": "\n\n".join(self.directory.format_city(city) for city in limited)
            + "\n\n<i>Kaynak: Arelbot sürümlü yerel şehir veri seti</i>",
            "data": [city.as_dict() for city in limited],
            "source": "Arelbot local city snapshot",
        }
