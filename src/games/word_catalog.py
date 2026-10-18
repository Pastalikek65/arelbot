"""Deterministic, safe and metadata-rich Turkish word bank for games.

The catalog is deliberately local: a round never needs an HTTP request or an AI
call.  The public API can be used by Anlatmaca, Adam Asmaca, Kelime Türetme,
Wordle-like rounds and quiz adapters without coupling those games to storage or
Telegram handlers.
"""

from __future__ import annotations

from dataclasses import dataclass
import random
import re
import unicodedata
from typing import Iterable, Sequence


# A small repair table keeps old JSON snapshots usable when they were decoded as
# latin-1/Windows-1252 before reaching the game layer.
_MOJIBAKE_REPLACEMENTS = {
    "\u00c3\u00a7": "\u00e7", "\u00c3\u00c7": "\u00c7", "\u00c4\u00b1": "\u0131", "\u00c4\u00b0": "\u0130", "\u00c3\u00b6": "\u00f6", "\u00c3\u00d6": "\u00d6",
    "\u00c3\u00bc": "\u00fc", "\u00c3\u00dc": "\u00dc", "\u00c5\u0178": "\u015f", "\u00c5\u015e": "\u015e", "\u00c4\u0178": "\u011f", "\u00c4\u011e": "\u011e",
    "\u00c3\u00a2": "\u00e2", "\u00c3\u00ae": "\u00ee", "\u00c3\u00bb": "\u00fb", "\u00c3\u00a9": "\u00e9", "\u00c2": "",
}

_UNSAFE_TOKENS = {
    "amk", "aq", "siktir", "sikeyim", "orospu", "piç", "pic",
    "yarrak", "göt", "got", "pezevenk", "şerefsiz", "serefsiz", "tecavüz",
    "tecavuz", "porn", "porno", "nazi", "soykırım", "soykirim",
}
_CATEGORY_ALIASES = {
    "cografya": "coğrafya", "geography": "coğrafya", "harita": "coğrafya",
    "doga": "doğa", "nature": "doğa", "sehir": "şehirler", "şehir": "şehirler",
    "city": "şehirler", "yemek": "yemekler", "food": "yemekler",
    "teknoloji": "teknoloji", "tech": "teknoloji", "bilim": "bilim",
    "science": "bilim", "genel kultur": "genel kültür", "kultur": "genel kültür",
    "kültür": "genel kültür", "edebiyat": "edebiyat", "sanat": "sanat",
    "spor": "spor", "tarih": "tarih", "hayvan": "hayvanlar", "hayvanlar": "hayvanlar",
    "meslek": "meslekler", "meslekler": "meslekler", "uzay": "uzay",
}
_DIFFICULTY_ALIASES = {
    "easy": "kolay", "kolay": "kolay", "beginner": "kolay", "normal": "orta",
    "medium": "orta", "orta": "orta", "hard": "zor", "zor": "zor",
    "expert": "uzman", "uzman": "uzman",
}


def _repair_mojibake(value: object) -> str:
    text = str(value or "")
    for broken, fixed in _MOJIBAKE_REPLACEMENTS.items():
        text = text.replace(broken, fixed)
    return text


def normalize_turkish(value: object) -> str:
    """Return a case/diacritic-insensitive search key.

    Turkish ``ı`` is intentionally folded to ``i`` so both keyboard layouts and
    old ASCII snapshots match.  Punctuation and whitespace are omitted to make
    command arguments and search terms behave consistently.
    """

    text = _repair_mojibake(value).casefold().replace("ı", "i")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char) and char.isalnum())


def normalize_category(value: object) -> str | None:
    key = normalize_turkish(value)
    if not key:
        return None
    # Compare aliases after normalizing their Turkish characters as well.
    for alias, canonical in _CATEGORY_ALIASES.items():
        if normalize_turkish(alias) == key:
            return canonical
    return str(value).strip().casefold() if str(value).strip() else None


def normalize_difficulty(value: object) -> str | None:
    key = normalize_turkish(value)
    for alias, canonical in _DIFFICULTY_ALIASES.items():
        if normalize_turkish(alias) == key:
            return canonical
    return None


def _unsafe_text(value: object) -> bool:
    raw = _repair_mojibake(value)
    if "<script" in raw.casefold() or "javascript:" in raw.casefold() or "http://" in raw.casefold() or "https://" in raw.casefold():
        return True
    tokens = set(re.findall(r"[\wçğıöşüÇĞİÖŞÜ]+", raw.casefold(), flags=re.UNICODE))
    normalized_tokens = {normalize_turkish(token) for token in tokens}
    return bool(normalized_tokens & {normalize_turkish(token) for token in _UNSAFE_TOKENS})


@dataclass(frozen=True, slots=True)
class WordEntry:
    """One answer and its answer-safe, human-curated clue metadata."""

    key: str
    word: str
    category: str
    difficulty: str
    description: str
    hints: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", normalize_category(self.category) or "genel kültür")
        object.__setattr__(self, "difficulty", normalize_difficulty(self.difficulty) or "orta")
        object.__setattr__(self, "hints", tuple(str(item).strip() for item in self.hints if str(item).strip()))
        object.__setattr__(self, "tags", tuple(str(item).strip() for item in self.tags if str(item).strip()))

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key, "word": self.word, "category": self.category,
            "difficulty": self.difficulty, "description": self.description,
            "hints": list(self.hints), "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "WordEntry":
        return cls(
            key=str(payload.get("key") or normalize_turkish(payload.get("word"))),
            word=str(payload.get("word") or "").strip(),
            category=str(payload.get("category") or "genel kültür"),
            difficulty=str(payload.get("difficulty") or "orta"),
            description=str(payload.get("description") or "").strip(),
            hints=tuple(str(item) for item in (payload.get("hints") or ()) if str(item).strip()),
            tags=tuple(str(item) for item in (payload.get("tags") or ()) if str(item).strip()),
        )


def safe_word_entry(entry: WordEntry) -> bool:
    """Validate content before it can enter a public game round."""

    word = _repair_mojibake(entry.word).strip()
    answer_key = normalize_turkish(word)
    if not re.fullmatch(r"[\wçğıöşüÇĞİÖŞÜ][\wçğıöşüÇĞİÖŞÜ '\-]{2,63}", word, flags=re.UNICODE):
        return False
    if not answer_key or _unsafe_text(word) or _unsafe_text(entry.description):
        return False
    if len(answer_key) < 3 or answer_key.isdigit():
        return False
    # A clue must never contain the answer as a word or an unbroken key.
    for clue in (entry.description, *entry.hints, *entry.tags):
        if _unsafe_text(clue):
            return False
        clue_key = normalize_turkish(clue)
        if answer_key in clue_key:
            return False
    return True


@dataclass(frozen=True, slots=True)
class DrawResult:
    entry: WordEntry
    cycle_reset: bool
    remaining: int


class WordCatalog:
    """Deterministic catalog and per-scope repeat guard for word games."""

    def __init__(self, entries: Iterable[WordEntry] | None = None, *, seed: int = 0) -> None:
        source = tuple(entries or DEFAULT_WORDS)
        clean: list[WordEntry] = []
        seen: set[str] = set()
        for entry in source:
            if not isinstance(entry, WordEntry) or not safe_word_entry(entry):
                continue
            identity = normalize_turkish(entry.key or entry.word)
            if identity and identity not in seen:
                seen.add(identity)
                clean.append(entry)
        self._entries = tuple(clean)
        self._seed = int(seed)
        self._rng = random.Random(self._seed)
        self._used: dict[tuple[str, str | None, str | None], set[str]] = {}

    def entries(self) -> tuple[WordEntry, ...]:
        return self._entries

    def categories(self) -> tuple[str, ...]:
        return tuple(sorted({entry.category for entry in self._entries}, key=normalize_turkish))

    def by_category(self, category: object) -> tuple[WordEntry, ...]:
        canonical = normalize_category(category)
        return tuple(entry for entry in self._entries if canonical is None or entry.category == canonical)

    def by_difficulty(self, difficulty: object) -> tuple[WordEntry, ...]:
        canonical = normalize_difficulty(difficulty)
        return tuple(entry for entry in self._entries if canonical is None or entry.difficulty == canonical)

    def search(self, query: object, *, category: object | None = None) -> tuple[WordEntry, ...]:
        needle = normalize_turkish(query)
        pool = self.by_category(category) if category is not None else self._entries
        if not needle:
            return pool
        return tuple(
            entry for entry in pool
            if needle in normalize_turkish(entry.word)
            or needle in normalize_turkish(entry.description)
            or any(needle in normalize_turkish(tag) for tag in entry.tags)
        )

    def hints_for(self, entry: WordEntry | str) -> tuple[str, ...]:
        if isinstance(entry, str):
            matches = self.search(entry)
            if not matches:
                return ()
            entry = matches[0]
        if not safe_word_entry(entry):
            return ()
        word = _repair_mojibake(entry.word).strip()
        key = normalize_turkish(word)
        letters = [char for char in word if char.isalpha()]
        vowels = sum(normalize_turkish(char) in "aeiou" for char in letters)
        # The order is intentional: broad semantic clue first, structure later.
        generated = [
            entry.description,
            f"{entry.category.title()} başlığından bir kavramdır.",
            f"{len(letters)} harflidir.",
            f"{letters[0].upper()} harfiyle başlar.",
            f"{letters[-1].upper()} harfiyle biter.",
            f"İçinde {vowels} sesli harf vardır.",
        ]
        if len(letters) >= 5:
            mask = " ".join([letters[0].upper(), *(["_"] * (len(letters) - 2)), letters[-1].upper()])
            generated.append(f"Harf şablonu: {mask}.")
        else:
            generated.append(f"İlk harfi {letters[0].upper()}, son harfi {letters[-1].upper()}.")
        result: list[str] = []
        seen: set[str] = set()
        for clue in (*entry.hints, *generated):
            clean = str(clue).strip()
            clue_key = normalize_turkish(clean)
            if not clean or clue_key in seen or key in clue_key:
                continue
            seen.add(clue_key)
            result.append(clean)
        return tuple(result)

    def draw(
        self,
        *,
        scope: str = "global",
        category: object | None = None,
        difficulty: object | None = None,
    ) -> DrawResult:
        canonical_category = normalize_category(category) if category is not None else None
        canonical_difficulty = normalize_difficulty(difficulty) if difficulty is not None else None
        pool = [
            entry for entry in self._entries
            if (canonical_category is None or entry.category == canonical_category)
            and (canonical_difficulty is None or entry.difficulty == canonical_difficulty)
        ]
        if not pool:
            # A requested difficulty may have no entries in a small custom catalog;
            # category fallback keeps a game playable without silently mixing chats.
            pool = [entry for entry in self._entries if canonical_category is None or entry.category == canonical_category]
        if not pool:
            raise LookupError("word_catalog_empty")
        token = (str(scope), canonical_category, canonical_difficulty)
        used = self._used.setdefault(token, set())
        available = [entry for entry in pool if entry.key not in used]
        cycle_reset = not available
        if cycle_reset:
            used.clear()
            available = list(pool)
        entry = available[self._rng.randrange(len(available))]
        used.add(entry.key)
        return DrawResult(entry=entry, cycle_reset=cycle_reset, remaining=len(pool) - len(used))


# Curated seed bank.  Descriptions and tags intentionally avoid spelling the
# answer so they can be shown before a guess.  The bank is expanded in code,
# keeping deployment independent from an external dictionary or scraper.
def _entry(key: str, word: str, category: str, difficulty: str, description: str, hints: Sequence[str], *tags: str) -> WordEntry:
    letters = [char for char in word if char.isalpha()]
    vowels = sum(normalize_turkish(char) in "aeiou" for char in letters)
    structural = (
        f"{len(letters)} harflidir.",
        f"{letters[0].upper()} harfiyle başlar.",
        f"{letters[-1].upper()} harfiyle biter.",
        f"İçinde {vowels} sesli harf vardır.",
    )
    merged = tuple(dict.fromkeys((*hints, *structural)))
    return WordEntry(key, word, category, difficulty, description, merged, tuple(tags))


DEFAULT_WORDS: tuple[WordEntry, ...] = (
    _entry("ankara", "Ankara", "şehirler", "kolay", "İç Anadolu'da yer alan başkent.", ("Türkiye'nin yönetim merkezidir.", "Kara iklimiyle bilinir."), "türkiye", "başkent"),
    _entry("istanbul", "İstanbul", "şehirler", "kolay", "İki kıtayı birleştiren kalabalık metropol.", ("Boğazın iki yakasına yayılır.", "Tarihi yarımadası ünlüdür."), "türkiye", "boğaz"),
    _entry("izmir", "İzmir", "şehirler", "kolay", "Ege kıyısında, körfeziyle tanınan şehir.", ("Saat kulesiyle anılır.", "Ege'nin büyük merkezlerindendir."), "türkiye", "ege"),
    _entry("trabzon", "Trabzon", "şehirler", "orta", "Karadeniz kıyısında yemyeşil bir şehir.", ("Sümela Manastırı bu bölgededir.", "Hamsiköyüyle de tanınır."), "türkiye", "karadeniz"),
    _entry("antalya", "Antalya", "şehirler", "kolay", "Akdeniz kıyısında turizmi güçlü şehir.", ("Sahilleri ve antik kentleriyle bilinir.", "Yaz tatilinin klasik adresidir."), "türkiye", "akdeniz"),
    _entry("kapadokya", "Kapadokya", "şehirler", "orta", "Peribacaları ve balonlarıyla ünlü tarihi bölge.", ("Göreme çevresindedir.", "Volkanik kayaların şekillendirdiği bir coğrafyadır."), "türkiye", "peribacası"),
    _entry("marmara", "Marmara", "coğrafya", "orta", "Türkiye'nin kuzeybatısındaki iç deniz ve çevresi.", ("İki boğaz arasında yer alır.", "Sanayi ve nüfus yoğunluğu yüksektir."), "bölge", "deniz"),
    _entry("delta", "Delta", "coğrafya", "zor", "Akarsuyun taşıdığı malzemeyle oluşan üçgensi kıyı şekli.", ("Akarsu ağzında görülür.", "Verimli tarım alanları oluşturabilir."), "yer şekli"),
    _entry("meridyen", "Meridyen", "coğrafya", "zor", "Kutupları birleştiren varsayımsal çizgi.", ("Boylam hesabında kullanılır.", "Başlangıç çizgisi Greenwich'ten geçer."), "harita"),
    _entry("muson", "Muson", "coğrafya", "uzman", "Mevsime göre yön değiştiren bölgesel rüzgâr düzeni.", ("Güney ve Güneydoğu Asya'da belirgindir.", "Yağış rejimini güçlü biçimde etkiler."), "iklim"),
    _entry("falez", "Falez", "coğrafya", "zor", "Dalgaların aşındırmasıyla oluşan dik kıyı.", ("Deniz kenarında yüksek duvar gibi görünür.", "Aşınım şekillerinden biridir."), "kıyı"),
    _entry("ekvator", "Ekvator", "coğrafya", "orta", "Dünyayı kuzey ve güney yarım küreye ayıran çizgi.", ("Sıfır derece enlem kabul edilir.", "Çevresinde sıcak kuşak görülür."), "enlem"),
    _entry("pusula", "Pusula", "bilim", "kolay", "Yön bulmaya yarayan manyetik araç.", ("İğnesi kuzeyi gösterir.", "Denizcilik ve keşiflerde kullanılmıştır."), "yön", "fizik"),
    _entry("atom", "Atom", "bilim", "orta", "Maddenin kimyasal özelliklerini taşıyan temel birim.", ("Çekirdek ve elektronlardan oluşur.", "Elementlerin yapı taşı sayılır."), "kimya"),
    _entry("enzim", "Enzim", "bilim", "zor", "Canlılarda tepkimeleri hızlandıran biyolojik katalizör.", ("Protein yapılı olanları yaygındır.", "Sindirimde önemli görev üstlenir."), "biyoloji"),
    _entry("kuantum", "Kuantum", "bilim", "uzman", "Mikro dünyadaki enerji ve madde davranışlarını anlatan yaklaşım.", ("Belirsizlik ve olasılıkla birlikte anılır.", "Klasik sezgileri zorlayan bir fizik alanıdır."), "fizik"),
    _entry("galaksi", "Galaksi", "uzay", "orta", "Yıldızlar, gaz ve tozdan oluşan dev kozmik yapı.", ("Samanyolu bunun bir örneğidir.", "Milyarlarca yıldız barındırabilir."), "evren"),
    _entry("bulutsu", "Bulutsu", "uzay", "zor", "Uzayda gaz ve tozun yoğunlaştığı geniş yapı.", ("Yeni yıldızların doğum yeri olabilir.", "Fotoğraflarda renkli bulutlar gibi görünür."), "astronomi"),
    _entry("karadelik", "Karadelik", "uzay", "uzman", "Çekimi ışığın kaçmasına izin vermeyecek kadar güçlü kozmik bölge.", ("Olay ufku kavramıyla birlikte anılır.", "Doğrudan görülemez, etkileri gözlenebilir."), "astronomi"),
    _entry("yörünge", "Yörünge", "uzay", "zor", "Bir gökcisminin başka bir cisim çevresindeki izlediği yol.", ("Uydu hareketlerini açıklamakta kullanılır.", "Elips biçimli olabilir."), "astronomi"),
    _entry("evrim", "Evrim", "bilim", "zor", "Canlı topluluklarının nesiller boyunca değişimini açıklayan süreç.", ("Doğal seçilimle birlikte düşünülür.", "Kalıtsal farklılıklar önemlidir."), "biyoloji"),
    _entry("algoritma", "Algoritma", "teknoloji", "orta", "Bir problemi çözmek için izlenen sonlu ve düzenli adımlar.", ("Kodlamanın temel planıdır.", "Girdi ve çıktı ilişkisini düzenler."), "yazılım"),
    _entry("şifreleme", "Şifreleme", "teknoloji", "zor", "Bilgiyi yetkisiz kişilerin okuyamayacağı biçime dönüştürme işlemi.", ("Anahtar kavramı önemlidir.", "Gizlilik ve bütünlükte kullanılır."), "güvenlik"),
    _entry("veritabanı", "Veritabanı", "teknoloji", "orta", "Düzenli bilgilerin saklandığı ve sorgulandığı sistem.", ("Tablo veya belge yapısı kullanabilir.", "Uygulamaların hafızası gibi çalışır."), "yazılım"),
    _entry("yapayzeka", "Yapay zekâ", "teknoloji", "orta", "Makinelerin öğrenme ve karar verme yeteneklerini araştıran alan.", ("Veriden örüntü çıkarabilir.", "Dil ve görüntü uygulamalarında kullanılır."), "makine öğrenmesi"),
    _entry("uydu", "Uydu", "uzay", "kolay", "Bir gezegen ya da gökcisminin çevresinde dönen cisim.", ("Doğal veya insan yapımı olabilir.", "İletişim ve gözlemde kullanılır."), "astronomi"),
    _entry("manyetik", "Manyetik", "bilim", "zor", "Mıknatıs ve alan etkileriyle ilişkili fiziksel özellik.", ("Kuzey ve güney kutuplarıyla anılır.", "Demir gibi metalleri etkileyebilir."), "fizik"),
    _entry("cumhuriyet", "Cumhuriyet", "tarih", "orta", "Egemenliğin halka ait olduğu yönetim biçimi.", ("Seçim ve yurttaşlık kavramlarıyla ilişkilidir.", "Türkiye'de 1923'te ilan edilmiştir."), "yönetim"),
    _entry("rönesans", "Rönesans", "tarih", "zor", "Avrupa'da sanat ve düşüncenin yeniden canlandığı dönem.", ("İtalya şehirleriyle başlatılır.", "Bilim ve sanat insanlarıyla anılır."), "avrupa"),
    _entry("demokrasi", "Demokrasi", "tarih", "orta", "Halkın yönetime doğrudan ya da temsilcileriyle katıldığı düzen.", ("Oy verme hakkıyla ilişkilidir.", "Çoğulculuk ve katılımı önemser."), "yönetim"),
    _entry("ipekyolu", "İpek Yolu", "tarih", "orta", "Asya ile Avrupa'yı bağlayan tarihî ticaret güzergâhı.", ("Kervanlar ve şehirler arasında uzanır.", "Değerli kumaş ticaretiyle ünlenmiştir."), "ticaret"),
    _entry("matbaa", "Matbaa", "tarih", "orta", "Metinleri çok sayıda çoğaltmayı sağlayan baskı düzeni.", ("Bilginin yayılmasını hızlandırdı.", "Gutenberg adıyla da anılır."), "teknoloji"),
    _entry("arkeoloji", "Arkeoloji", "tarih", "zor", "Geçmiş toplumların maddi kalıntılarını inceleyen bilim.", ("Kazılar ve eserler temel kaynağıdır.", "Toprak altındaki hikâyeleri ortaya çıkarır."), "bilim"),
    _entry("miras", "Miras", "tarih", "kolay", "Geçmişten bugüne aktarılan kültürel ya da maddi değer.", ("Koruma ve aktarım kavramlarıyla birlikte anılır.", "Bir toplumun hafızasını taşıyabilir."), "kültür"),
    _entry("göbeklitepe", "Göbeklitepe", "tarih", "zor", "Şanlıurfa yakınlarında bulunan çok eski anıtsal alan.", ("Neolitik dönemle ilişkilidir.", "T biçimli taşlarıyla tanınır."), "arkeoloji"),
    _entry("baklava", "Baklava", "yemekler", "kolay", "İnce katmanlı hamur, kuruyemiş ve şerbetle hazırlanan tatlı.", ("Bayram sofralarında sık görülür.", "Gaziantep mutfağıyla güçlü biçimde anılır."), "tatlı"),
    _entry("mantı", "Mantı", "yemekler", "kolay", "Küçük hamur parçalarının iç harçla doldurulduğu yemek.", ("Yoğurt ve sosla servis edilir.", "Kayseri usulü meşhurdur."), "hamur işi"),
    _entry("tarhana", "Tarhana", "yemekler", "orta", "Fermente edilip kurutulan karışımdan yapılan çorba.", ("Kış hazırlıklarıyla ilişkilidir.", "Ekşi aromasıyla bilinir."), "çorba"),
    _entry("zeytinyağı", "Zeytinyağı", "yemekler", "orta", "Zeytin meyvesinden elde edilen bitkisel yağ.", ("Akdeniz mutfağının temel ürünüdür.", "Salata ve sıcak yemeklerde kullanılır."), "ege"),
    _entry("simit", "Simit", "yemekler", "kolay", "Susamla kaplanan halka biçimli fırın ürünü.", ("Çay eşlikçisi olarak bilinir.", "Sokak lezzetlerinin klasiklerinden biridir."), "kahvaltı"),
    _entry("şerbet", "Şerbet", "yemekler", "orta", "Su, şeker ve aroma veren malzemelerle hazırlanan içecek.", ("Osmanlı mutfağında önemli yeri vardır.", "Gül ya da meyve aromalı olabilir."), "içecek"),
    _entry("keşkek", "Keşkek", "yemekler", "zor", "Buğday ve etin uzun süre pişirilmesiyle yapılan geleneksel yemek.", ("Düğün sofralarında görülür.", "Dövme buğday kullanılan yöresel bir tariftir."), "anadolu"),
    _entry("kelebek", "Kelebek", "hayvanlar", "kolay", "Kanatları pullarla kaplı, başkalaşım geçiren uçucu böcek.", ("Tırtıl evresinden geçer.", "Çiçeklerden nektar alabilir."), "böcek"),
    _entry("ahtapot", "Ahtapot", "hayvanlar", "orta", "Sekiz kollu, zeki ve denizde yaşayan yumuşakça.", ("Kamuflaj yeteneği güçlüdür.", "Mürekkep püskürterek kaçabilir."), "deniz"),
    _entry("penguen", "Penguen", "hayvanlar", "kolay", "Uçamayan, yüzmeye uyumlu deniz kuşu.", ("Güney yarım küreyle anılır.", "Siyah beyaz gövdesi vardır."), "kuş"),
    _entry("yarasa", "Yarasa", "hayvanlar", "orta", "Aktif uçuş yapabilen tek memeli grubu.", ("Ekolokasyon kullanabilir.", "Gece etkinliğiyle bilinir."), "memeli"),
    _entry("bukalemun", "Bukalemun", "hayvanlar", "zor", "Renk ve göz hareketleriyle dikkat çeken sürüngen.", ("Dili gövdesinden uzun olabilir.", "Her renk değişimi yalnızca kamuflaj değildir."), "sürüngen"),
    _entry("arı", "Arı", "hayvanlar", "kolay", "Çiçeklerden beslenip koloni halinde yaşayan böcek.", ("Tozlaşmada önemli rol oynar.", "Bal üretimiyle tanınır."), "böcek"),
    _entry("orkide", "Orkide", "doğa", "orta", "Gösterişli çiçekleri olan geniş bitki ailesi.", ("Süs bitkisi olarak yetiştirilir.", "Bazı türleri tropik iklimleri sever."), "bitki"),
    _entry("şelale", "Şelale", "doğa", "kolay", "Suyun yüksekten aşağı döküldüğü doğal oluşum.", ("Akarsu yatağında görülür.", "Sesi ve manzarasıyla dikkat çeker."), "su"),
    _entry("çığ", "Çığ", "doğa", "orta", "Kar kütlesinin yamaçtan hızla aşağı kayması.", ("Eğim ve kar birikimi riski artırır.", "Dağlık bölgelerde uyarı konusu olabilir."), "afet"),
    _entry("mangrov", "Mangrov", "doğa", "zor", "Tuzlu kıyı sularında yetişen ağaç ve çalı topluluğu.", ("Kıyı ekosistemini korur.", "Tropik bölgelerde yaygındır."), "ekoloji"),
    _entry("gökkuşağı", "Gökkuşağı", "doğa", "kolay", "Işığın su damlalarında kırılmasıyla görülen renkli yay.", ("Yağmur sonrası güneşle birlikte belirir.", "Renkleri belirli bir sırayla görünür."), "hava"),
    _entry("empati", "Empati", "genel kültür", "orta", "Bir başkasının duygusunu anlamaya çalışma yeteneği.", ("Dinleme ve perspektif değiştirmeyle güçlenir.", "İletişimde güven oluşturur."), "duygu"),
    _entry("merak", "Merak", "genel kültür", "kolay", "Bir şeyi öğrenmeye veya anlamaya yönelten istek.", ("Soruların yakıtı gibidir.", "Bilimsel keşifleri besler."), "duygu"),
    _entry("adalet", "Adalet", "genel kültür", "orta", "Hak ve hukuka uygun davranma ilkesi.", ("Eşitlik ve hakkaniyetle ilişkilidir.", "Toplumsal düzenin temel kavramlarından biridir."), "değer"),
    _entry("strateji", "Strateji", "genel kültür", "zor", "Bir hedefe ulaşmak için tasarlanan uzun vadeli plan.", ("Kaynak ve zaman yönetimi ister.", "Oyunlarda ve yönetimde kullanılır."), "planlama"),
    _entry("dayanışma", "Dayanışma", "genel kültür", "orta", "İnsanların ortak amaç için birbirine destek olması.", ("Zor zamanda görünür hale gelir.", "Topluluk bağlarını güçlendirir."), "toplum"),
    _entry("ironi", "İroni", "edebiyat", "zor", "Söylenen ile kastedilen arasında bilinçli karşıtlık kurma.", ("Bağlamı anlamayı gerektirir.", "Mizah ve eleştiride sık görülür."), "anlatım"),
    _entry("metafor", "Metafor", "edebiyat", "zor", "Bir kavramı başka bir kavramın özellikleriyle anlatma.", ("Benzetmeden daha örtük olabilir.", "Şiirsel anlatımı yoğunlaştırır."), "anlatım"),
    _entry("roman", "Roman", "edebiyat", "kolay", "Uzun olay örgüsü ve karakterler içeren kurmaca eser.", ("Bölümler ve anlatıcılar içerebilir.", "Kitaplıkların klasik türlerindendir."), "kitap"),
    _entry("şiir", "Şiir", "edebiyat", "kolay", "Duygu ve düşünceleri yoğun, ritmik dille anlatan tür.", ("Dize ve ölçü kavramlarıyla ilişkilidir.", "Kafiye kullanımı mümkün ama zorunlu değildir."), "kitap"),
    _entry("alegori", "Alegori", "edebiyat", "uzman", "Bir düşünceyi baştan sona simgesel anlatımla aktarma.", ("Karakter ve olaylar başka anlamlara işaret eder.", "Masal ve fablda görülebilir."), "anlatım"),
    _entry("heykel", "Heykel", "sanat", "kolay", "Taş, metal veya başka malzemeyle üç boyutlu eser üretme.", ("Yontma ve biçimlendirme teknikleri kullanılır.", "Müze ve meydanlarda sergilenebilir."), "görsel sanat"),
    _entry("perspektif", "Perspektif", "sanat", "zor", "Derinlik ve uzaklık etkisini yüzeyde verme tekniği.", ("Resimde bakış noktasını düzenler.", "Ufuk çizgisiyle birlikte düşünülür."), "resim"),
    _entry("mozaik", "Mozaik", "sanat", "orta", "Küçük parçaların bir araya getirilmesiyle oluşturulan görsel eser.", ("Taş, cam ya da seramik parçaları kullanılır.", "Antik yapılarda sık rastlanır."), "görsel sanat"),
    _entry("senfoni", "Senfoni", "sanat", "zor", "Orkestra için yazılmış çok bölümlü büyük müzik eseri.", ("Klasik müzik repertuvarında önemli yeri vardır.", "Birden fazla bölümden oluşur."), "müzik"),
    _entry("tiyatro", "Tiyatro", "sanat", "kolay", "Sahnede oyuncularla canlandırılan dramatik sanat.", ("Perde ve sahne kavramlarıyla ilişkilidir.", "Seyirci karşısında gerçekleşir."), "sahne"),
    _entry("maraton", "Maraton", "spor", "orta", "Uzun mesafeli dayanıklılık koşusu.", ("Olimpik atletizm programındadır.", "Mesafesi kırk iki kilometreyi aşar."), "koşu"),
    _entry("ofsayt", "Ofsayt", "spor", "zor", "Futbolda hücum oyuncusunun konumuyla ilgili kural ihlali.", ("Pas anındaki konum dikkate alınır.", "Hakem yardımcısının bayrağıyla işaretlenebilir."), "futbol"),
    _entry("servis", "Servis", "spor", "orta", "Tenis veya voleybolda oyunu başlatan vuruş.", ("Topu rakip alana göndermekle başlar.", "Kuralları spora göre değişir."), "raket"),
    _entry("penaltı", "Penaltı", "spor", "orta", "Futbolda ceza alanındaki ihlal sonrası kullanılan vuruş.", ("Kaleci ve tek vuruşla ilişkilidir.", "Maçın kaderini değiştirebilir."), "futbol"),
    _entry("şampiyon", "Şampiyon", "spor", "kolay", "Bir yarışma veya sezonu birinci bitiren kişi ya da takım.", ("Kupa ve madalyayla ödüllendirilebilir.", "Liderliği tamamlayan unvandır."), "başarı"),
    _entry("buharlastirma", "Buharlaştırma", "bilim", "orta", "Bir sıvıyı ısı etkisiyle gaz hâline geçirme işlemi.", ("Kaynama ve ısı aktarımıyla ilişkilidir.", "Karışımları ayırmada kullanılabilir."), "kimya"),
    _entry("fotosentez", "Fotosentez", "bilim", "orta", "Bitkilerin ışık enerjisiyle besin üretme süreci.", ("Klorofil ve ışıkla ilişkilidir.", "Yan ürün olarak oksijen açığa çıkar."), "biyoloji"),
    _entry("jeoloji", "Jeoloji", "bilim", "orta", "Dünyanın yapısını ve kayaçlarını inceleyen bilim.", ("Katmanlar ve minerallerle çalışır.", "Depremleri anlamaya katkı sağlar."), "yer bilimi"),
    _entry("frekans", "Frekans", "bilim", "orta", "Bir olayın belirli sürede kaç kez tekrarlandığını anlatan ölçü.", ("Hertz birimiyle ifade edilir.", "Ses ve dalga çalışmalarında kullanılır."), "fizik"),
    _entry("büyüteç", "Büyüteç", "bilim", "kolay", "Küçük nesneleri daha büyük gösteren optik araç.", ("Mercek kullanır.", "İnceleme ve hobi işlerinde işe yarar."), "optik"),
    _entry("basketbol", "Basketbol", "spor", "kolay", "Topun rakip potaya atıldığı takım sporu.", ("Beşer kişilik ekiplerle oynanır.", "Dripling ve şut temel hareketlerdir."), "takım sporu"),
    _entry("voleybol", "Voleybol", "spor", "kolay", "File üzerinden topla oynanan takım sporu.", ("Topa arka arkaya sınırlı sayıda dokunulur.", "Servisle başlayan rallilerden oluşur."), "takım sporu"),
    _entry("seramik", "Seramik", "sanat", "orta", "Kil benzeri malzemenin şekillendirilip pişirilmesiyle yapılan eser.", ("Fırınlama dayanıklılık kazandırır.", "Çömlekçilikle yakın ilişkilidir."), "zanaat"),
    _entry("fotoğraf", "Fotoğraf", "sanat", "kolay", "Işığı bir yüzeye kaydederek görüntü oluşturma işi.", ("Kadraj ve pozlama önemlidir.", "Makine veya telefonla çekilebilir."), "görsel sanat"),
    _entry("biyografi", "Biyografi", "edebiyat", "orta", "Bir kişinin yaşamını anlatan gerçek temelli eser.", ("Yazar başka bir kişinin hayatını inceler.", "Tarih ve anılar kaynak olabilir."), "kitap"),
    _entry("fabl", "Fabl", "edebiyat", "kolay", "Çoğu kez hayvanları konuşturarak ders veren kısa anlatı.", ("Sonunda öğüt bulunabilir.", "Ezop adıyla birlikte anılır."), "öykü"),
)


__all__ = [
    "DEFAULT_WORDS", "DrawResult", "WordCatalog", "WordEntry", "normalize_category",
    "normalize_difficulty", "normalize_turkish", "safe_word_entry",
]

