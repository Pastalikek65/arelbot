"""Fast local Turkish province and district directory.

The data file is a versioned snapshot.  Runtime queries never require an
external network request, which makes city lookup usable during provider
outages and in private chats with strict latency requirements.
"""

from __future__ import annotations

import json
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_TR_TRANSLATION = str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u"})


def normalize_text(value: str) -> str:
    # Apply Turkish casing before diacritic stripping so short forms such as
    # ``ist`` and names such as ``Şanlıurfa`` resolve consistently.
    value = value.translate(str.maketrans({
        "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i",
        "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
    }))
    value = unicodedata.normalize("NFKD", value.strip().casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.translate(_TR_TRANSLATION)
    return re.sub(r"[^a-z0-9]+", "", value)


@dataclass(frozen=True)
class CityRecord:
    id: str
    name: str
    plate: int
    phone: tuple[int, ...]
    region: str | None
    districts: tuple[str, ...]
    latitude: float | None = None
    longitude: float | None = None
    source: str | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "il": self.name,
            "plaka": self.plate,
            "telefon": list(self.phone),
            "bolge": self.region,
            "ilceler": list(self.districts),
            "koordinat": {"lat": self.latitude, "lon": self.longitude},
            "kaynak": self.source,
        }


class CityDirectory:
    def __init__(self, data_path: str | Path | None = None) -> None:
        self.data_path = Path(data_path or Path(__file__).resolve().parents[1] / "data" / "turkish_cities.json")
        self._cities: tuple[CityRecord, ...] = ()
        self._by_name: dict[str, CityRecord] = {}
        self._by_plate: dict[int, CityRecord] = {}
        self._by_phone: dict[int, list[CityRecord]] = {}
        self._by_district: dict[str, list[CityRecord]] = {}
        self.reload()

    def reload(self) -> None:
        payload = json.loads(self.data_path.read_text(encoding="utf-8"))
        cities: list[CityRecord] = []
        items = payload.get("cities", []) if isinstance(payload, dict) else payload
        expected_checksum = payload.get("checksum_sha256") if isinstance(payload, dict) else None
        if expected_checksum:
            canonical = json.dumps(
                sorted(items, key=lambda item: int(item.get("plate", 0))),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            actual_checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if actual_checksum != expected_checksum:
                raise ValueError("City snapshot checksum mismatch")
        for item in items:
            record = CityRecord(
                id=str(item.get("id", item.get("plate", ""))),
                name=str(item["name"]),
                plate=int(item["plate"]),
                phone=tuple(int(code) for code in item.get("phone", [])),
                region=item.get("region"),
                districts=tuple(str(value) for value in item.get("districts", [])),
                latitude=float(item["latitude"]) if item.get("latitude") is not None else None,
                longitude=float(item["longitude"]) if item.get("longitude") is not None else None,
                source=item.get("source"),
            )
            cities.append(record)

        self._cities = tuple(sorted(cities, key=lambda city: city.plate))
        self._by_name = {normalize_text(city.name): city for city in self._cities}
        self._by_plate = {city.plate: city for city in self._cities}
        self._by_phone = {}
        self._by_district = {}
        for city in self._cities:
            for phone in city.phone:
                self._by_phone.setdefault(phone, []).append(city)
            for district in city.districts:
                self._by_district.setdefault(normalize_text(district), []).append(city)

    def all(self) -> tuple[CityRecord, ...]:
        return self._cities

    def by_name(self, name: str) -> CityRecord | None:
        return self._by_name.get(normalize_text(name))

    def by_plate(self, plate: int | str) -> CityRecord | None:
        try:
            return self._by_plate.get(int(plate))
        except (TypeError, ValueError):
            return None

    def by_phone(self, phone: int | str) -> tuple[CityRecord, ...]:
        try:
            return tuple(self._by_phone.get(int(phone), []))
        except (TypeError, ValueError):
            return ()

    def by_district(self, district: str) -> tuple[CityRecord, ...]:
        return tuple(self._by_district.get(normalize_text(district), []))

    def search(self, query: str, limit: int = 8) -> tuple[CityRecord, ...]:
        needle = normalize_text(query)
        if not needle:
            return self._cities[:limit]
        exact = self._by_name.get(needle)
        if exact:
            return (exact,)
        matches = [city for city in self._cities if needle in normalize_text(city.name)]
        if len(matches) < limit:
            for city in self._cities:
                if any(needle in normalize_text(district) for district in city.districts):
                    if city not in matches:
                        matches.append(city)
                if len(matches) >= limit:
                    break
        return tuple(matches[:limit])

    def search_page(self, query: str, *, offset: int = 0, limit: int = 20) -> tuple[tuple[CityRecord, ...], bool]:
        """Return one deterministic page and whether another page exists.

        Inline callers must not approximate pagination by asking ``search``
        for only ``limit + 1`` rows: a broad Turkish prefix can match more
        than one page. The snapshot is small and local, so building the
        complete bounded match list here is deterministic and cheap.
        """

        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(int(limit), 100))
        needle = normalize_text(query)
        if not needle:
            matches = list(self._cities)
        else:
            exact = self._by_name.get(needle)
            matches = [exact] if exact else [city for city in self._cities if needle in normalize_text(city.name)]
            if exact is None:
                for city in self._cities:
                    if any(needle in normalize_text(district) for district in city.districts):
                        if city not in matches:
                            matches.append(city)
        page = tuple(matches[safe_offset : safe_offset + safe_limit])
        return page, safe_offset + safe_limit < len(matches)

    def resolve(self, query: str) -> tuple[CityRecord, ...]:
        query = query.strip()
        by_name = self.by_name(query)
        if by_name:
            return (by_name,)
        by_plate = self.by_plate(query)
        if by_plate:
            return (by_plate,)
        by_phone = self.by_phone(query)
        if by_phone:
            return by_phone
        return self.by_district(query)

    @staticmethod
    def format_city(city: CityRecord) -> str:
        districts = ", ".join(city.districts) if city.districts else "Veri setinde ilçe listesi yok"
        phone = ", ".join(str(code) for code in city.phone) or "-"
        region = city.region or "-"
        return (
            f"<b>{city.name}</b>\n"
            f"Plaka: <code>{city.plate:02d}</code>\n"
            f"Telefon: <code>{phone}</code>\n"
            f"Bölge: {region}\n"
            f"İlçeler: {districts}"
        )
