"""Build Arelbot's local Turkish province/district snapshot.

Sources:
* Province and district names/coordinates: turkiye-data (MIT), which records
  turkiye-api as its upstream source.
* Telephone area codes: BTK National Numbering Plan.

The generated file is intentionally a normalized application snapshot.  The
script is repeatable and can be run during a controlled data update.
"""

from __future__ import annotations

import json
import hashlib
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src" / "data" / "turkish_cities.json"
BASE = "https://raw.githubusercontent.com/mumeyyizbuyuk/turkiye-data/main/data/"

# Vehicle plate identifiers are stable Turkish administrative identifiers.
PLATES = {
    "Adana": 1, "AdÄ±yaman": 2, "Afyonkarahisar": 3, "AÄŸrÄ±": 4, "Amasya": 5,
    "Ankara": 6, "Antalya": 7, "Artvin": 8, "AydÄ±n": 9, "BalÄ±kesir": 10,
    "Bilecik": 11, "BingÃ¶l": 12, "Bitlis": 13, "Bolu": 14, "Burdur": 15,
    "Bursa": 16, "Ã‡anakkale": 17, "Ã‡ankÄ±rÄ±": 18, "Ã‡orum": 19, "Denizli": 20,
    "DiyarbakÄ±r": 21, "Edirne": 22, "ElazÄ±ÄŸ": 23, "Erzincan": 24, "Erzurum": 25,
    "EskiÅŸehir": 26, "Gaziantep": 27, "Giresun": 28, "GÃ¼mÃ¼ÅŸhane": 29, "Hakkari": 30,
    "Hatay": 31, "Isparta": 32, "Mersin": 33, "Ä°stanbul": 34, "Ä°zmir": 35,
    "Kars": 36, "Kastamonu": 37, "Kayseri": 38, "KÄ±rklareli": 39, "KÄ±rÅŸehir": 40,
    "Kocaeli": 41, "Konya": 42, "KÃ¼tahya": 43, "Malatya": 44, "Manisa": 45,
    "KahramanmaraÅŸ": 46, "Mardin": 47, "MuÄŸla": 48, "MuÅŸ": 49, "NevÅŸehir": 50,
    "NiÄŸde": 51, "Ordu": 52, "Rize": 53, "Sakarya": 54, "Samsun": 55,
    "Siirt": 56, "Sinop": 57, "Sivas": 58, "TekirdaÄŸ": 59, "Tokat": 60,
    "Trabzon": 61, "Tunceli": 62, "ÅanlÄ±urfa": 63, "UÅŸak": 64, "Van": 65,
    "Yozgat": 66, "Zonguldak": 67, "Aksaray": 68, "Bayburt": 69, "Karaman": 70,
    "KÄ±rÄ±kkale": 71, "Batman": 72, "ÅÄ±rnak": 73, "BartÄ±n": 74, "Ardahan": 75,
    "IÄŸdÄ±r": 76, "Yalova": 77, "KarabÃ¼k": 78, "Kilis": 79, "Osmaniye": 80,
    "DÃ¼zce": 81,
}

PHONE = {
    "Adana": [322], "AdÄ±yaman": [416], "Afyonkarahisar": [272], "AÄŸrÄ±": [472], "Aksaray": [382],
    "Amasya": [358], "Ankara": [312], "Antalya": [242], "Ardahan": [478], "Artvin": [466],
    "AydÄ±n": [256], "BalÄ±kesir": [266], "BartÄ±n": [378], "Batman": [488], "Bayburt": [458],
    "Bilecik": [228], "BingÃ¶l": [426], "Bitlis": [434], "Bolu": [374], "Burdur": [248],
    "Bursa": [224], "Ã‡anakkale": [286], "Ã‡ankÄ±rÄ±": [376], "Ã‡orum": [364], "Denizli": [258],
    "DiyarbakÄ±r": [412], "DÃ¼zce": [380], "Edirne": [284], "ElazÄ±ÄŸ": [424], "Erzincan": [446],
    "Erzurum": [442], "EskiÅŸehir": [222], "Gaziantep": [342], "Giresun": [454], "GÃ¼mÃ¼ÅŸhane": [456],
    "Hakkari": [438], "Hatay": [326], "IÄŸdÄ±r": [476], "Isparta": [246], "Mersin": [324],
    "Ä°stanbul": [212, 216], "Ä°zmir": [232], "KarabÃ¼k": [370], "Karaman": [338], "Kars": [474],
    "Kastamonu": [366], "Kayseri": [352], "KÄ±rÄ±kkale": [318], "KÄ±rklareli": [288], "KÄ±rÅŸehir": [386],
    "Kilis": [348], "KahramanmaraÅŸ": [344], "Kocaeli": [262], "Konya": [332], "KÃ¼tahya": [274],
    "Malatya": [422], "Manisa": [236], "Mardin": [482], "MuÄŸla": [252], "MuÅŸ": [436],
    "NevÅŸehir": [384], "NiÄŸde": [388], "Ordu": [452], "Osmaniye": [328], "Rize": [464],
    "Sakarya": [264], "Samsun": [362], "Siirt": [484], "Sinop": [368], "Sivas": [346],
    "ÅanlÄ±urfa": [414], "ÅÄ±rnak": [486], "TekirdaÄŸ": [282], "Tokat": [356], "Trabzon": [462],
    "Tunceli": [428], "UÅŸak": [276], "Van": [432], "Yalova": [226], "Yozgat": [354], "Zonguldak": [372],
}

REGIONS = {
    "Adana": "Akdeniz", "AdÄ±yaman": "GÃ¼neydoÄŸu Anadolu", "Afyonkarahisar": "Ege", "AÄŸrÄ±": "DoÄŸu Anadolu",
    "Aksaray": "Ä°Ã§ Anadolu", "Amasya": "Karadeniz", "Ankara": "Ä°Ã§ Anadolu", "Antalya": "Akdeniz",
    "Ardahan": "DoÄŸu Anadolu", "Artvin": "Karadeniz", "AydÄ±n": "Ege", "BalÄ±kesir": "Marmara",
    "BartÄ±n": "Karadeniz", "Batman": "GÃ¼neydoÄŸu Anadolu", "Bayburt": "Karadeniz", "Bilecik": "Marmara",
    "BingÃ¶l": "DoÄŸu Anadolu", "Bitlis": "DoÄŸu Anadolu", "Bolu": "Karadeniz", "Burdur": "Akdeniz",
    "Bursa": "Marmara", "Ã‡anakkale": "Marmara", "Ã‡ankÄ±rÄ±": "Ä°Ã§ Anadolu", "Ã‡orum": "Karadeniz",
    "Denizli": "Ege", "DiyarbakÄ±r": "GÃ¼neydoÄŸu Anadolu", "DÃ¼zce": "Karadeniz", "Edirne": "Marmara",
    "ElazÄ±ÄŸ": "DoÄŸu Anadolu", "Erzincan": "DoÄŸu Anadolu", "Erzurum": "DoÄŸu Anadolu", "EskiÅŸehir": "Ä°Ã§ Anadolu",
    "Gaziantep": "GÃ¼neydoÄŸu Anadolu", "Giresun": "Karadeniz", "GÃ¼mÃ¼ÅŸhane": "Karadeniz", "Hakkari": "DoÄŸu Anadolu",
    "Hatay": "Akdeniz", "IÄŸdÄ±r": "DoÄŸu Anadolu", "Isparta": "Akdeniz", "Ä°stanbul": "Marmara",
    "Ä°zmir": "Ege", "KarabÃ¼k": "Karadeniz", "Karaman": "Ä°Ã§ Anadolu", "Kars": "DoÄŸu Anadolu",
    "Kastamonu": "Karadeniz", "Kayseri": "Ä°Ã§ Anadolu", "KÄ±rÄ±kkale": "Ä°Ã§ Anadolu", "KÄ±rklareli": "Marmara",
    "KÄ±rÅŸehir": "Ä°Ã§ Anadolu", "Kilis": "GÃ¼neydoÄŸu Anadolu", "KahramanmaraÅŸ": "Akdeniz", "Kocaeli": "Marmara",
    "Konya": "Ä°Ã§ Anadolu", "KÃ¼tahya": "Ege", "Malatya": "DoÄŸu Anadolu", "Manisa": "Ege",
    "Mardin": "GÃ¼neydoÄŸu Anadolu", "Mersin": "Akdeniz", "MuÄŸla": "Ege", "MuÅŸ": "DoÄŸu Anadolu",
    "NevÅŸehir": "Ä°Ã§ Anadolu", "NiÄŸde": "Ä°Ã§ Anadolu", "Ordu": "Karadeniz", "Osmaniye": "Akdeniz",
    "Rize": "Karadeniz", "Sakarya": "Marmara", "Samsun": "Karadeniz", "Siirt": "GÃ¼neydoÄŸu Anadolu",
    "Sinop": "Karadeniz", "Sivas": "Ä°Ã§ Anadolu", "ÅanlÄ±urfa": "GÃ¼neydoÄŸu Anadolu", "ÅÄ±rnak": "GÃ¼neydoÄŸu Anadolu",
    "TekirdaÄŸ": "Marmara", "Tokat": "Karadeniz", "Trabzon": "Karadeniz", "Tunceli": "DoÄŸu Anadolu",
    "UÅŸak": "Ege", "Van": "DoÄŸu Anadolu", "Yalova": "Marmara", "Yozgat": "Ä°Ã§ Anadolu", "Zonguldak": "Karadeniz",
}


def load_json(name: str):
    request = urllib.request.Request(BASE + name, headers={"User-Agent": "Arelbot-city-builder/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main() -> None:
    provinces = load_json("provinces.json")
    districts = load_json("districts.json")
    by_province: dict[int, list[str]] = defaultdict(list)
    for district in districts:
        by_province[int(district["province_id"])].append(district["name"])

    cities = []
    for province in provinces:
        name = province["name"]
        if name not in PLATES:
            raise RuntimeError(f"Missing plate mapping for {name}")
        coords = province.get("coordinates") or {}
        cities.append({
            "id": f"TR-{PLATES[name]:02d}",
            "name": name,
            "plate": PLATES[name],
            "phone": PHONE[name],
            "region": REGIONS[name],
            "districts": sorted(by_province[int(province["id"])], key=str.casefold),
            "latitude": coords.get("latitude"),
            "longitude": coords.get("longitude"),
            "source": "turkiye-data (MIT) / BTK National Numbering Plan",
        })

    city_payload = json.dumps(sorted(cities, key=lambda item: item["plate"]), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload = {
        "schema_version": 1,
        "snapshot_version": date.today().isoformat(),
        "generated_at": date.today().isoformat(),
        "checksum_sha256": hashlib.sha256(city_payload.encode("utf-8")).hexdigest(),
        "source": {
            "administrative": "https://github.com/mumeyyizbuyuk/turkiye-data",
            "administrative_license": "MIT",
            "phone_codes": "https://www.btk.gov.tr/en/national-numbering-plan",
        },
        "cities": sorted(cities, key=lambda item: item["plate"]),
    }
    if OUTPUT.exists():
        OUTPUT.with_suffix(OUTPUT.suffix + ".bak").write_bytes(OUTPUT.read_bytes())
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(cities)} provinces and {len(districts)} districts to {OUTPUT}")


if __name__ == "__main__":
    main()
