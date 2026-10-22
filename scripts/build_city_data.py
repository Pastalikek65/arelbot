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
    "Adana": 1, "Adıyaman": 2, "Afyonkarahisar": 3, "Ağrı": 4, "Amasya": 5,
    "Ankara": 6, "Antalya": 7, "Artvin": 8, "Aydın": 9, "Balıkesir": 10,
    "Bilecik": 11, "Bingöl": 12, "Bitlis": 13, "Bolu": 14, "Burdur": 15,
    "Bursa": 16, "Çanakkale": 17, "Çankırı": 18, "Çorum": 19, "Denizli": 20,
    "Diyarbakır": 21, "Edirne": 22, "Elazığ": 23, "Erzincan": 24, "Erzurum": 25,
    "Eskişehir": 26, "Gaziantep": 27, "Giresun": 28, "Gümüşhane": 29, "Hakkari": 30,
    "Hatay": 31, "Isparta": 32, "Mersin": 33, "İstanbul": 34, "İzmir": 35,
    "Kars": 36, "Kastamonu": 37, "Kayseri": 38, "Kırklareli": 39, "Kırşehir": 40,
    "Kocaeli": 41, "Konya": 42, "Kütahya": 43, "Malatya": 44, "Manisa": 45,
    "Kahramanmaraş": 46, "Mardin": 47, "Muğla": 48, "Muş": 49, "Nevşehir": 50,
    "Niğde": 51, "Ordu": 52, "Rize": 53, "Sakarya": 54, "Samsun": 55,
    "Siirt": 56, "Sinop": 57, "Sivas": 58, "Tekirdağ": 59, "Tokat": 60,
    "Trabzon": 61, "Tunceli": 62, "Şanlıurfa": 63, "Uşak": 64, "Van": 65,
    "Yozgat": 66, "Zonguldak": 67, "Aksaray": 68, "Bayburt": 69, "Karaman": 70,
    "Kırıkkale": 71, "Batman": 72, "Şırnak": 73, "Bartın": 74, "Ardahan": 75,
    "Iğdır": 76, "Yalova": 77, "Karabük": 78, "Kilis": 79, "Osmaniye": 80,
    "Düzce": 81,
}

PHONE = {
    "Adana": [322], "Adıyaman": [416], "Afyonkarahisar": [272], "Ağrı": [472], "Aksaray": [382],
    "Amasya": [358], "Ankara": [312], "Antalya": [242], "Ardahan": [478], "Artvin": [466],
    "Aydın": [256], "Balıkesir": [266], "Bartın": [378], "Batman": [488], "Bayburt": [458],
    "Bilecik": [228], "Bingöl": [426], "Bitlis": [434], "Bolu": [374], "Burdur": [248],
    "Bursa": [224], "Çanakkale": [286], "Çankırı": [376], "Çorum": [364], "Denizli": [258],
    "Diyarbakır": [412], "Düzce": [380], "Edirne": [284], "Elazığ": [424], "Erzincan": [446],
    "Erzurum": [442], "Eskişehir": [222], "Gaziantep": [342], "Giresun": [454], "Gümüşhane": [456],
    "Hakkari": [438], "Hatay": [326], "Iğdır": [476], "Isparta": [246], "Mersin": [324],
    "İstanbul": [212, 216], "İzmir": [232], "Karabük": [370], "Karaman": [338], "Kars": [474],
    "Kastamonu": [366], "Kayseri": [352], "Kırıkkale": [318], "Kırklareli": [288], "Kırşehir": [386],
    "Kilis": [348], "Kahramanmaraş": [344], "Kocaeli": [262], "Konya": [332], "Kütahya": [274],
    "Malatya": [422], "Manisa": [236], "Mardin": [482], "Muğla": [252], "Muş": [436],
    "Nevşehir": [384], "Niğde": [388], "Ordu": [452], "Osmaniye": [328], "Rize": [464],
    "Sakarya": [264], "Samsun": [362], "Siirt": [484], "Sinop": [368], "Sivas": [346],
    "Şanlıurfa": [414], "Şırnak": [486], "Tekirdağ": [282], "Tokat": [356], "Trabzon": [462],
    "Tunceli": [428], "Uşak": [276], "Van": [432], "Yalova": [226], "Yozgat": [354], "Zonguldak": [372],
}

REGIONS = {
    "Adana": "Akdeniz", "Adıyaman": "Güneydoğu Anadolu", "Afyonkarahisar": "Ege", "Ağrı": "Doğu Anadolu",
    "Aksaray": "İç Anadolu", "Amasya": "Karadeniz", "Ankara": "İç Anadolu", "Antalya": "Akdeniz",
    "Ardahan": "Doğu Anadolu", "Artvin": "Karadeniz", "Aydın": "Ege", "Balıkesir": "Marmara",
    "Bartın": "Karadeniz", "Batman": "Güneydoğu Anadolu", "Bayburt": "Karadeniz", "Bilecik": "Marmara",
    "Bingöl": "Doğu Anadolu", "Bitlis": "Doğu Anadolu", "Bolu": "Karadeniz", "Burdur": "Akdeniz",
    "Bursa": "Marmara", "Çanakkale": "Marmara", "Çankırı": "İç Anadolu", "Çorum": "Karadeniz",
    "Denizli": "Ege", "Diyarbakır": "Güneydoğu Anadolu", "Düzce": "Karadeniz", "Edirne": "Marmara",
    "Elazığ": "Doğu Anadolu", "Erzincan": "Doğu Anadolu", "Erzurum": "Doğu Anadolu", "Eskişehir": "İç Anadolu",
    "Gaziantep": "Güneydoğu Anadolu", "Giresun": "Karadeniz", "Gümüşhane": "Karadeniz", "Hakkari": "Doğu Anadolu",
    "Hatay": "Akdeniz", "Iğdır": "Doğu Anadolu", "Isparta": "Akdeniz", "İstanbul": "Marmara",
    "İzmir": "Ege", "Karabük": "Karadeniz", "Karaman": "İç Anadolu", "Kars": "Doğu Anadolu",
    "Kastamonu": "Karadeniz", "Kayseri": "İç Anadolu", "Kırıkkale": "İç Anadolu", "Kırklareli": "Marmara",
    "Kırşehir": "İç Anadolu", "Kilis": "Güneydoğu Anadolu", "Kahramanmaraş": "Akdeniz", "Kocaeli": "Marmara",
    "Konya": "İç Anadolu", "Kütahya": "Ege", "Malatya": "Doğu Anadolu", "Manisa": "Ege",
    "Mardin": "Güneydoğu Anadolu", "Mersin": "Akdeniz", "Muğla": "Ege", "Muş": "Doğu Anadolu",
    "Nevşehir": "İç Anadolu", "Niğde": "İç Anadolu", "Ordu": "Karadeniz", "Osmaniye": "Akdeniz",
    "Rize": "Karadeniz", "Sakarya": "Marmara", "Samsun": "Karadeniz", "Siirt": "Güneydoğu Anadolu",
    "Sinop": "Karadeniz", "Sivas": "İç Anadolu", "Şanlıurfa": "Güneydoğu Anadolu", "Şırnak": "Güneydoğu Anadolu",
    "Tekirdağ": "Marmara", "Tokat": "Karadeniz", "Trabzon": "Karadeniz", "Tunceli": "Doğu Anadolu",
    "Uşak": "Ege", "Van": "Doğu Anadolu", "Yalova": "Marmara", "Yozgat": "İç Anadolu", "Zonguldak": "Karadeniz",
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
