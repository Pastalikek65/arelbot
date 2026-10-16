"""Local Turkish vocabulary shared by the word games.

The game bank is deliberately local and dependency-free. Entries are stored
in ASCII-compatible transliteration so deployments cannot lose Turkish
characters during a shell or archive conversion; the normalizer accepts both
forms from users.
"""

from __future__ import annotations

from collections import Counter
import unicodedata


WORD_GAME_VOCABULARY: dict[str, tuple[str, ...]] = {
    "hayvanlar": (
        "ari", "aslan", "at", "ayi", "balina", "baykus", "boga", "ceylan",
        "cekirge", "deve", "dogan", "fil", "fare", "geyik", "horoz", "inek",
        "kaplan", "karga", "kartal", "keci", "kelebek", "kedi", "kopek",
        "kurbaga", "leylek", "maymun", "penguen", "sincap", "tilki", "tavsan",
        "zurafa", "yunus", "yilan", "ordek", "sinek", "kirpi", "koala",
        "koyun", "timsah", "deli", "deri", "lider", "kiler", "ileri", "erik",
        "diler", "keder",
    ),
    "yemekler": (
        "asure", "baklava", "balik", "bamya", "bazlama", "begendi", "biber",
        "borek", "bulgur", "cacik", "corba", "doner", "ekmek", "etli",
        "fasulye", "gozleme", "hamur", "helva", "hunkar", "kabak",
        "karniyarik", "kebap", "kesmek", "kofte", "lahmacun", "makarna",
        "manti", "mercimek", "pilav", "pide", "salata", "sarma", "sebze",
        "simit", "tatli", "tarhana", "yogurt", "zeytin", "nohut", "patates",
        "ankara", "arama", "marka", "karma", "masa", "nar", "sarman", "ramak",
    ),
    "\u015fehirler": (
        "ankara", "antalya", "ardahan", "artvin", "aydin", "balikesir",
        "bartin", "bayburt", "bilecik", "bingol", "bitlis", "bolu", "bursa",
        "canakkale", "cankiri", "corum", "denizli", "diyarbakir", "edirne",
        "elazig", "erzincan", "erzurum", "eskisehir", "istanbul", "izmir",
        "kastamonu", "kayseri", "kilis", "konya", "kutahya", "malatya",
        "manisa", "mardin", "mersin", "mugla", "mus", "nevsehir", "ordu",
        "rize", "sakarya", "samsun", "siirt", "sinop", "sivas", "sanliurfa",
        "tekirdag", "tokat", "trabzon", "tunceli", "usak", "van", "yalova",
        "yozgat", "zonguldak", "sultan", "bina", "ilan", "bal", "bul", "ani",
        "altin",
    ),
    "do\u011fa": (
        "agac", "akarsu", "alacakaranlik", "arazi", "bahar", "bataklik",
        "bozkir", "bulut", "cayir", "col", "dag", "dere", "diken", "doga",
        "gol", "gokkusagi", "gunes", "hava", "irmak", "iklim", "kayalik",
        "kanyon", "kis", "kumsal", "magara", "mese", "nehir", "orman",
        "ova", "pinar", "ruzgar", "sahil", "selale", "toprak", "vadi",
        "yagmur", "yaprak", "yaz", "yildirim", "zemin", "cam", "cimen",
        "cicek", "sonbahar", "roma", "anlam", "normal", "roman", "maral",
        "alarm", "oral", "nar", "arma",
    ),
    "bilim": (
        "atom", "astronomi", "biyoloji", "buyutec", "cekirdek", "deney",
        "enerji", "enzim", "evrim", "fizik", "formul", "galaksi", "genetik",
        "hucre", "isik", "ivme", "kimya", "laboratuvar", "madde", "matematik",
        "mekanik", "meteor", "molekul", "noron", "optik", "paleontoloji",
        "parcacik", "planck", "reaksiyon", "robotik", "surec", "teori",
        "teleskop", "termodinamik", "uzay", "veri", "yercekimi", "yorunge",
        "zaman", "kuantum", "ekoloji", "bilim", "bile", "bir", "bel", "emir",
        "elim", "ileri", "mil", "ilim", "bil", "eller", "ekim",
    ),
    "spor": (
        "atlet", "basketbol", "boks", "ceviklik", "defans", "file", "futbol",
        "gures", "hakem", "halter", "hentbol", "hiz", "isabet", "judo",
        "kaptan", "karate", "kros", "maraton", "mac", "mizrak", "pas",
        "penalti", "puan", "raket", "rekor", "ring", "saha", "savunma", "set",
        "skor", "smac", "sporcu", "stadyum", "sampiyon", "takim", "tempo",
        "tenis", "tur", "voleybol", "yaris", "zafer", "ziplama", "cizgi",
        "tribun", "spor", "soru", "sular", "oral", "sarp", "pus", "pul",
        "sor", "pas", "sur",
    ),
    "sanat": (
        "afis", "anlati", "arsiv", "bale", "beste", "cizim", "dekor", "desen",
        "drama", "edebiyat", "eser", "estetik", "firca", "fotograf", "galeri",
        "heykel", "ilham", "kamera", "kanvas", "karakter", "koro", "kumas",
        "melodi", "mimari", "minyatur", "muzik", "opera", "oyuncu", "palet",
        "perde", "portre", "resim", "ritim", "sahne", "senaryo", "siir",
        "sarki", "tasarim", "tiyatro", "ton", "uslup", "yonetmen", "yuzey",
        "sergi", "emir", "isim", "elim", "esmer", "serim", "sil", "ser",
        "mese",
    ),
    "tarih": (
        "akin", "antik", "arsiv", "ayaklanma", "baris", "baskent", "belge",
        "beylik", "cumhuriyet", "cag", "devrim", "diplomasi", "donanma",
        "elci", "fetih", "hanedan", "imparatorluk", "inkilap", "isyan", "kale",
        "krallik", "kultur", "medeniyet", "meclis", "miras", "monarsi", "ordu",
        "ronesans", "savas", "sefer", "selcuklu", "sultan", "sehir", "tarih",
        "taht", "toplum", "ulusal", "uygarlik", "vakif", "yasa", "zafer",
        "onder", "osmanli", "roma", "misal", "sal", "mal", "mil", "sin",
        "nasil", "islam", "ilan", "mas",
    ),
    "teknoloji": (
        "algoritma", "ag", "arayuz", "arama", "batarya", "bilgisayar", "bulut",
        "cip", "cihaz", "cozum", "donanim", "ekran", "guvenlik", "internet",
        "islemci", "kablosuz", "kod", "komut", "kullanici", "klavye", "makine",
        "model", "mobil", "modem", "uygulama", "robot", "program", "protokol",
        "sanal", "sunucu", "sifre", "tablet", "tarayici", "teknoloji",
        "veritabani", "yazilim", "yapayzeka", "yigin", "zeka", "cevrim",
        "dosya", "depolama", "sensor", "bilgi", "saygi", "say", "asil", "gibi",
        "bas", "bil", "ayi", "bay", "yasi",
    ),
    "meslekler": (
        "asci", "avukat", "bankaci", "berber", "bilimci", "boyaci", "ciftci",
        "doktor", "eczaci", "editor", "elektrikci", "firinci", "garson",
        "gazeteci", "hemsire", "hakim", "hostes", "isci", "jandarma", "kasiyer",
        "kaynakci", "kimyager", "kaptan", "kuafor", "mimar", "muhendis",
        "muzisyen", "ogretmen", "pilot", "polis", "programci", "psikolog",
        "ressam", "sofor", "savci", "sekreter", "sivaci", "sporcu", "terzi",
        "tasarimci", "teknisyen", "veteriner", "yazar", "yonetici", "doktor",
        "motor", "kurum", "komut", "koro", "ordu", "tur", "kor", "tok", "kum",
        "otur", "oku",
    ),
    "uzay": (
        "ay", "astronot", "atmosfer", "asteroit", "baslangic", "bulutsu",
        "ceres", "cekim", "dunya", "evren", "gezegen", "gokada", "goktasi",
        "gunes", "isik", "jupiter", "karadelik", "kuyrukluyildiz", "mars",
        "merkur", "meteor", "neptun", "nova", "pluton", "roket", "saturn",
        "uydu", "uranus", "uzay", "venus", "yorunge", "yildiz", "zaman",
        "teleskop", "kozmos", "samanyolu", "isin", "krater", "yercekimi",
        "galaksi", "fuze", "istasyon", "kesif", "ari", "ayi", "yaz", "yari",
        "diyar", "diri", "dar", "zil", "zira",
    ),
    "genel": (
        "adalet", "akil", "amac", "analiz", "anlam", "ani", "arzu", "basari",
        "beceri", "bilgi", "birlik", "cesaret", "caba", "cozum", "deger",
        "denge", "deneyim", "dostluk", "dusunce", "emek", "endise", "fikir",
        "firsat", "gelecek", "gercek", "gelenek", "girisim", "gorev", "hayal",
        "hedef", "hikaye", "huzur", "irade", "istek", "kanit", "karar",
        "kalite", "kavram", "kaynak", "kural", "lider", "merak", "mutluluk",
        "neden", "niyet", "ozgurluk", "plan", "sabir", "sorumluluk", "saygi",
        "sevgi", "sonuc", "soz", "sure", "tavir", "tecrube", "umut",
        "uzlasma", "vicdan", "yetenek", "yontem", "zihin", "aliskanlik",
        "anlasma", "butce", "dakika", "duzen", "etki", "fayda", "guven",
        "hiz", "ihtiyac", "kapsam", "mantik", "mesaj", "olay", "oncelik",
        "sebep", "secim", "tavsiye", "kelime", "elim", "emir", "kim", "kil",
        "mil", "kiler", "lime", "ekler", "eller", "krem", "ekim",
    ),
}


WORD_GAME_RACKS: dict[str, str] = {
    "hayvanlar": "KED\u0130LER",
    "yemekler": "MAKARNASI",
    "\u015fehirler": "\u0130STANBUL",
    "do\u011fa": "ORMANLAR",
    "bilim": "B\u0130L\u0130MLER",
    "spor": "SPORCULAR",
    "sanat": "RES\u0130MLER",
    "tarih": "OSMANLILAR",
    "teknoloji": "B\u0130LG\u0130SAYAR",
    "meslekler": "DOKTORLAR",
    "uzay": "YILDIZLAR",
    "genel": "KEL\u0130MELER",
}


def normalize_game_word(value: object) -> str:
    """Normalise Turkish case and diacritics for fair answer comparison."""

    text = str(value or "").strip().casefold().replace("Ä±", "i")
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def all_game_words() -> list[str]:
    """Return a stable, duplicate-free vocabulary across all categories."""

    result: list[str] = []
    seen: set[str] = set()
    for words in WORD_GAME_VOCABULARY.values():
        for word in words:
            normalized = normalize_game_word(word)
            if len(normalized) < 3 or not normalized.isalpha() or normalized in seen:
                continue
            seen.add(normalized)
            result.append(word)
    return result


def can_form_word(word: str, letters: str) -> bool:
    """Check whether every letter in *word* exists in the rack."""

    candidate = normalize_game_word(word)
    rack = normalize_game_word(letters)
    if len(candidate) < 3 or not candidate.isalpha():
        return False
    available = Counter(rack)
    return all(count <= available.get(char, 0) for char, count in Counter(candidate).items())


def words_for_rack(category: str, letters: str) -> list[str]:
    """Return category words plus common fallback words that fit a rack."""

    ordered: list[str] = []
    seen: set[str] = set()
    sources = (WORD_GAME_VOCABULARY.get(category, ()), tuple(all_game_words()))
    for words in sources:
        for word in words:
            normalized = normalize_game_word(word)
            if normalized in seen or not can_form_word(word, letters):
                continue
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def vocabulary_entries() -> list[dict[str, object]]:
    """Convert the local bank into safe clue entries for anlatmaca."""

    entries: list[dict[str, object]] = []
    for category, words in WORD_GAME_VOCABULARY.items():
        for word in words:
            normalized = normalize_game_word(word)
            if len(normalized) < 4:
                continue
            display = word.upper()
            entries.append(
                {
                    "word": display,
                    "category": category,
                    "description": "Bu turda seÃ§ilen, gunluk hayatta veya genel kulturde karsilasabilecegin bir kavram.",
                    "hints": [
                        f"{len(normalized)} harflidir.",
                        f"{display[0]} harfi ile baslar.",
                        f"{category.title()} basligiyla baglantilidir.",
                    ],
                }
            )
    return entries

