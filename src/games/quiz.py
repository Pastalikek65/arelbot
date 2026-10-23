
import hashlib
import random
import unicodedata
from typing import Dict, List, Optional, Any

from src.games.word_catalog import normalize_turkish

class QuizGame:
    """Interactive quiz with multiple choice, scoring, and rich categories."""

    def __init__(self, *, seed: int | None = None) -> None:
        # Seeded instances power deterministic replay tests; the default keeps
        # the existing random user experience.
        self._rng = random.Random(seed)
        self._used: dict[tuple[str, str], set[str]] = {}
        self._seed = seed
    
    QUESTIONS = {
        "tip": [
            {"q": "İnsan vücudundaki en büyük organ hangisidir?", "a": "Deri", "options": ["Deri", "Karaciğer", "Akciğer", "Bağırsak"]},
            {"q": "Yetişkin bir insanda kaç adet kemik bulunur?", "a": "206", "options": ["206", "186", "256", "196"]},
            {"q": "Kanı pompalayan organ hangisidir?", "a": "Kalp", "options": ["Kalp", "Akciğer", "Böbrek", "Beyin"]},
            {"q": "Hangi kan grubu 'Genel Verici' olarak bilinir?", "a": "0 Negatif", "options": ["0 Negatif", "AB Pozitif", "A Pozitif", "B Negatif"]},
            {"q": "DNA'nın açılımı nedir?", "a": "Deoksiribonükleik Asit", "options": ["Deoksiribonükleik Asit", "Dişsel Nükleer Asit", "Diatomik Nitrogen Asit", "Deoksi Nükleer Amino"]},
            {"q": "İnsan vücudunda en küçük kemik nerededir?", "a": "Kulak", "options": ["Kulak", "El", "Ayak", "Burun"]},
            {"q": "Kırmızı kan hücrelerinin ömrü yaklaşık kaç gündür?", "a": "120", "options": ["120", "60", "30", "365"]},
            {"q": "Dünyadaki ilk kalp nakli hangi yıl yapılmıştır?", "a": "1967", "options": ["1967", "1955", "1972", "1980"]},
            {"q": "İnsülin hangi organdan salgılanır?", "a": "Pankreas", "options": ["Pankreas", "Karaciğer", "Böbrek", "Dalak"]},
            {"q": "İnsan beyninin yaklaşık yüzde kaçı sudan oluşur?", "a": "75", "options": ["75", "50", "90", "60"]},
        ],
        "tarih": [
            {"q": "İstanbul hangi yıl fethedildi?", "a": "1453", "options": ["1453", "1472", "1389", "1501"]},
            {"q": "Türkiye Cumhuriyeti hangi yıl kuruldu?", "a": "1923", "options": ["1923", "1920", "1919", "1925"]},
            {"q": "İlk dünya savaşı hangi yıl başladı?", "a": "1914", "options": ["1914", "1912", "1918", "1910"]},
            {"q": "Mustafa Kemal Atatürk'ün doğum yılı nedir?", "a": "1881", "options": ["1881", "1878", "1885", "1880"]},
            {"q": "Fransız İhtilali hangi yıl gerçekleşmiştir?", "a": "1789", "options": ["1789", "1776", "1799", "1750"]},
            {"q": "Malazgirt Savaşı hangi yıl yapılmıştır?", "a": "1071", "options": ["1071", "1055", "1099", "1040"]},
            {"q": "Osmanlı İmparatorluğu hangi yıl kurulmuştur?", "a": "1299", "options": ["1299", "1280", "1320", "1350"]},
            {"q": "Kurtuluş Savaşı'nın son muharebesi hangisidir?", "a": "Büyük Taarruz", "options": ["Büyük Taarruz", "Sakarya", "İnönü", "Dumlupınar"]},
            {"q": "Berlin Duvarı hangi yıl yıkılmıştır?", "a": "1989", "options": ["1989", "1991", "1985", "1990"]},
            {"q": "Magna Carta hangi yıl imzalanmıştır?", "a": "1215", "options": ["1215", "1200", "1300", "1150"]},
        ],
        "cografya": [
            {"q": "Dünyanın en yüksek dağı hangisidir?", "a": "Everest", "options": ["Everest", "K2", "Kangchenjunga", "Ağrı Dağı"]},
            {"q": "Türkiye'nin başkenti neresidir?", "a": "Ankara", "options": ["Ankara", "İstanbul", "İzmir", "Bursa"]},
            {"q": "En büyük okyanus hangisidir?", "a": "Pasifik", "options": ["Pasifik", "Atlantik", "Hint", "Arktik"]},
            {"q": "Türkiye'nin en uzun nehri hangisidir?", "a": "Kızılırmak", "options": ["Kızılırmak", "Fırat", "Sakarya", "Dicle"]},
            {"q": "Dünyanın en küçük kıtası hangisidir?", "a": "Avustralya", "options": ["Avustralya", "Avrupa", "Antarktika", "Güney Amerika"]},
            {"q": "Dünyanın en derin gölü hangisidir?", "a": "Baykal Gölü", "options": ["Baykal Gölü", "Hazar Denizi", "Van Gölü", "Victoria Gölü"]},
            {"q": "Amazon Nehri hangi kıtadadır?", "a": "Güney Amerika", "options": ["Güney Amerika", "Afrika", "Asya", "Kuzey Amerika"]},
            {"q": "Sahara Çölü hangi kıtadadır?", "a": "Afrika", "options": ["Afrika", "Asya", "Avustralya", "Güney Amerika"]},
            {"q": "Karadeniz'i Marmara Denizi'ne bağlayan boğaz hangisidir?", "a": "İstanbul", "options": ["Çanakkale", "İstanbul", "Cebelitarık", "Hürmüz"]},
            {"q": "Japonya'nın başkenti neresidir?", "a": "Tokyo", "options": ["Tokyo", "Kyoto", "Osaka", "Seul"]},
        ],
        "spor": [
            {"q": "Futbol maçları kaç dakika sürer?", "a": "90", "options": ["90", "80", "120", "60"]},
            {"q": "Basketbolda bir takım sahada kaç kişiyle oynar?", "a": "5", "options": ["5", "6", "7", "4"]},
            {"q": "Olimpiyat halkaları kaç renkten oluşur?", "a": "5", "options": ["5", "6", "4", "7"]},
            {"q": "Dünya Kupası kaç yılda bir düzenlenir?", "a": "4", "options": ["4", "2", "3", "5"]},
            {"q": "Teniste 'sıfır' puanı hangi terimle ifade edilir?", "a": "Love", "options": ["Love", "Zero", "Nil", "Blank"]},
            {"q": "FIFA Dünya Kupası'nı en çok kazanan ülke?", "a": "Brezilya", "options": ["Brezilya", "Almanya", "İtalya", "Arjantin"]},
            {"q": "Bir maraton kaç km'dir?", "a": "42.195", "options": ["42.195", "40", "50", "38.5"]},
            {"q": "Voleybolda bir takım kaç kişi ile sahada oynar?", "a": "6", "options": ["6", "5", "7", "8"]},
            {"q": "2022 Dünya Kupası nerede yapılmıştır?", "a": "Katar", "options": ["Katar", "Rusya", "Brezilya", "Japonya"]},
            {"q": "Bir futbol takımında kaleci hariç kaç oyuncu vardır?", "a": "10", "options": ["10", "9", "11", "8"]},
        ],
        "bilim": [
            {"q": "Suyun kaynama noktası kaç derecedir?", "a": "100", "options": ["100", "90", "110", "80"]},
            {"q": "Periyodik tablonun ilk elementi nedir?", "a": "Hidrojen", "options": ["Hidrojen", "Helyum", "Lityum", "Karbon"]},
            {"q": "Yerçekimi kanununu kim bulmuştur?", "a": "Newton", "options": ["Newton", "Einstein", "Galileo", "Kepler"]},
            {"q": "Güneş sistemindeki en büyük gezegen hangisidir?", "a": "Jüpiter", "options": ["Jüpiter", "Satürn", "Uranüs", "Neptün"]},
            {"q": "Albert Einstein'ın en ünlü formülü nedir?", "a": "E=mc²", "options": ["E=mc²", "F=ma", "PV=nRT", "V=IR"]},
            {"q": "Işığın hızı saniyede yaklaşık kaç km'dir?", "a": "300.000", "options": ["300.000", "150.000", "500.000", "100.000"]},
            {"q": "Dünya'nın Güneş etrafındaki dönüşü kaç gün sürer?", "a": "365", "options": ["365", "360", "370", "354"]},
            {"q": "Hangi element sembolü 'O' dir?", "a": "Oksijen", "options": ["Oksijen", "Osmiyum", "Oganeson", "Altın"]},
            {"q": "Ay'a ilk ayak basan astronot kimdir?", "a": "Neil Armstrong", "options": ["Neil Armstrong", "Buzz Aldrin", "Yuri Gagarin", "John Glenn"]},
            {"q": "DNA'nın çift sarmal yapısını keşfeden bilim insanları?", "a": "Watson ve Crick", "options": ["Watson ve Crick", "Darwin ve Wallace", "Curie ve Pasteur", "Newton ve Leibniz"]},
        ],
        "sanat": [
            {"q": "Mona Lisa tablosunu kim yapmıştır?", "a": "Leonardo da Vinci", "options": ["Leonardo da Vinci", "Michelangelo", "Raphael", "Rembrandt"]},
            {"q": "Yıldızlı Gece tablosu kime aittir?", "a": "Van Gogh", "options": ["Van Gogh", "Monet", "Picasso", "Dalí"]},
            {"q": "Beethoven hangi iki müzik dönemi arasında köprü kabul edilir?", "a": "Klasik ve Romantik", "options": ["Klasik ve Romantik", "Barok ve Klasik", "Rönesans ve Barok", "Romantik ve Modern"]},
            {"q": "Hababam Sınıfı'nın yazarı kimdir?", "a": "Rıfat Ilgaz", "options": ["Rıfat Ilgaz", "Aziz Nesin", "Orhan Kemal", "Yaşar Kemal"]},
            {"q": "Guernica tablosunu kim yapmıştır?", "a": "Pablo Picasso", "options": ["Pablo Picasso", "Salvador Dalí", "Andy Warhol", "Claude Monet"]},
            {"q": "'Çığlık' tablosu hangi sanatçıya aittir?", "a": "Edvard Munch", "options": ["Edvard Munch", "Gustav Klimt", "Frida Kahlo", "René Magritte"]},
        ],
        "yazilim": [
            {"q": "Python dilini kim geliştirmiştir?", "a": "Guido van Rossum", "options": ["Guido van Rossum", "Linus Torvalds", "James Gosling", "Dennis Ritchie"]},
            {"q": "HTML'nin açılımı nedir?", "a": "HyperText Markup Language", "options": ["HyperText Markup Language", "High Tech Modern Language", "HyperText Machine Learning", "Home Tool Markup Language"]},
            {"q": "Linux çekirdeğini kim yazmıştır?", "a": "Linus Torvalds", "options": ["Linus Torvalds", "Richard Stallman", "Ken Thompson", "Dennis Ritchie"]},
            {"q": "Git versiyon kontrol sistemini kim geliştirmiştir?", "a": "Linus Torvalds", "options": ["Linus Torvalds", "Guido van Rossum", "Bill Gates", "Mark Zuckerberg"]},
            {"q": "JavaScript hangi yıl oluşturulmuştur?", "a": "1995", "options": ["1995", "2000", "1990", "1998"]},
            {"q": "SQL ne anlama gelir?", "a": "Structured Query Language", "options": ["Structured Query Language", "Simple Question Language", "Standard Query Logic", "System Query Layer"]},
        ],
        "sinema": [
            {"q": "Yüzüklerin Efendisi serisinin yönetmeni kimdir?", "a": "Peter Jackson", "options": ["Peter Jackson", "Steven Spielberg", "James Cameron", "Christopher Nolan"]},
            {"q": "Titanic filminin yönetmeni kimdir?", "a": "James Cameron", "options": ["James Cameron", "Ridley Scott", "Martin Scorsese", "Steven Spielberg"]},
            {"q": "Star Wars serisini yaratan kişi kimdir?", "a": "George Lucas", "options": ["George Lucas", "Steven Spielberg", "J.J. Abrams", "Ridley Scott"]},
            {"q": "Matrix filminin yönetmenleri kimlerdir?", "a": "Wachowski Kardeşler", "options": ["Wachowski Kardeşler", "Coen Kardeşler", "Russo Kardeşler", "Nolan Kardeşler"]},
            {"q": "Hangi film serisi 'Bir Yüzük hepsini yönetmek için' sloganıyla bilinir?", "a": "Yüzüklerin Efendisi", "options": ["Yüzüklerin Efendisi", "Harry Potter", "Hobbit", "Narnia"]},
            {"q": "Joker filminde başrolü hangi oyuncu oynamıştır?", "a": "Joaquin Phoenix", "options": ["Joaquin Phoenix", "Heath Ledger", "Jack Nicholson", "Jared Leto"]},
        ],
        "edebiyat": [
            {"q": "Savaş ve Barış romanının yazarı kimdir?", "a": "Tolstoy", "options": ["Tolstoy", "Dostoyevski", "Çehov", "Puşkin"]},
            {"q": "Suç ve Ceza romanının yazarı kimdir?", "a": "Dostoyevski", "options": ["Dostoyevski", "Tolstoy", "Gogol", "Turgenyev"]},
            {"q": "Küçük Prens kitabının yazarı kimdir?", "a": "Antoine de Saint-Exupéry", "options": ["Antoine de Saint-Exupéry", "Victor Hugo", "Jules Verne", "Albert Camus"]},
            {"q": "Romeo ve Juliet kimin eseridir?", "a": "Shakespeare", "options": ["Shakespeare", "Molière", "Goethe", "Dante"]},
            {"q": "Nutuk kimin eseridir?", "a": "Atatürk", "options": ["Atatürk", "İsmet İnönü", "Mehmet Akif Ersoy", "Tevfik Fikret"]},
            {"q": "Sabahattin Ali'nin en ünlü romanı hangisidir?", "a": "Kürk Mantolu Madonna", "options": ["Kürk Mantolu Madonna", "İçimizdeki Şeytan", "Kuyucaklı Yusuf", "Sinekli Bakkal"]},
        ],
        "muzik": [
            {"q": "Bir orkestrada ritmi yöneten kişiye ne denir?", "a": "Şef", "options": ["Şef", "Solist", "Aranjör", "Besteci"]},
            {"q": "Piyanoda standart olarak kaç tuş bulunur?", "a": "88", "options": ["88", "66", "76", "100"]},
            {"q": "Dört telli klasik yaylı çalgı hangisidir?", "a": "Keman", "options": ["Keman", "Flüt", "Piyano", "Klarnet"]},
            {"q": "Ritimde bir ölçünün düzenli vuruşlarına ne ad verilir?", "a": "Metre", "options": ["Metre", "Tını", "Akor", "Gam"]},
            {"q": "Türk sanat müziğinde kullanılan makam sistemine örnek hangisidir?", "a": "Hicaz", "options": ["Hicaz", "Barok", "Blues", "Reggae"]},
            {"q": "Bir eserin sözlerini yazan kişiye ne denir?", "a": "Söz yazarı", "options": ["Söz yazarı", "Prodüktör", "Tonmaister", "Solist"]},
        ],
        "turkiye": [
            {"q": "Türkiye'nin en kalabalık şehri hangisidir?", "a": "İstanbul", "options": ["İstanbul", "Ankara", "İzmir", "Bursa"]},
            {"q": "Türkiye'nin en uzun nehri hangisidir?", "a": "Kızılırmak", "options": ["Kızılırmak", "Fırat", "Dicle", "Sakarya"]},
            {"q": "Kapadokya hangi bölgemizdedir?", "a": "İç Anadolu", "options": ["İç Anadolu", "Ege", "Marmara", "Karadeniz"]},
            {"q": "Türkiye'nin para birimi nedir?", "a": "Türk lirası", "options": ["Türk lirası", "Euro", "Dinar", "Manat"]},
            {"q": "Anıtkabir hangi şehirdedir?", "a": "Ankara", "options": ["Ankara", "İstanbul", "Samsun", "Konya"]},
            {"q": "Pamukkale hangi ilimizdedir?", "a": "Denizli", "options": ["Denizli", "Muğla", "Aydın", "Antalya"]},
        ],
        "gundelik": [
            {"q": "Bir yıl normalde kaç gün sürer?", "a": "365", "options": ["365", "360", "364", "366"]},
            {"q": "Bir saatte kaç dakika vardır?", "a": "60", "options": ["60", "50", "100", "30"]},
            {"q": "Su deniz seviyesinde yaklaşık kaç derecede kaynar?", "a": "100", "options": ["100", "90", "80", "120"]},
            {"q": "Trafik ışıklarında durmayı belirten renk hangisidir?", "a": "Kırmızı", "options": ["Kırmızı", "Yeşil", "Mavi", "Sarı"]},
            {"q": "Bir düzinede kaç adet bulunur?", "a": "12", "options": ["12", "10", "14", "24"]},
            {"q": "Kahvaltı kelimesi hangi öğünden önce anlamına gelir?", "a": "Kahve", "options": ["Kahve", "Akşam", "Öğle", "Çay"]},
        ],
        "oyunlar": [
            {"q": "Satrançta her oyuncu oyuna kaç taşla başlar?", "a": "16", "options": ["16", "12", "18", "20"]},
            {"q": "Satrançta şahı tehdit eden duruma ne denir?", "a": "Şah", "options": ["Şah", "Mat", "Pat", "Rok"]},
            {"q": "Tavlada toplam kaç pul bulunur?", "a": "30", "options": ["30", "24", "15", "32"]},
            {"q": "Basketbolda üç sayı çizgisinin dışından atılan isabet kaç puandır?", "a": "3", "options": ["3", "2", "1", "4"]},
            {"q": "Connect Four'da kazanmak için kaç taş hizalanır?", "a": "4", "options": ["4", "3", "5", "6"]},
            {"q": "XOX oyununda bir oyuncunun taşı genellikle hangi semboldür?", "a": "X", "options": ["X", "O", "+", "*"]},
        ],    }

    @staticmethod
    def normalize_category(category: object) -> str:
        """Resolve Turkish labels and ASCII aliases without random fallback."""
        folded = normalize_turkish(category)
        aliases = {
            "tip": "tip", "saglik": "tip",
            "tarih": "tarih", "cografya": "cografya",
            "bilim": "bilim", "sanat": "sanat", "yazilim": "yazilim",
            "sinema": "sinema", "edebiyat": "edebiyat", "muzik": "muzik",
            "turkiye": "turkiye", "gundelik": "gundelik",
            "oyun": "oyunlar", "oyunlar": "oyunlar",
            "spor": "spor",
        }
        return aliases.get(folded, folded)

    @classmethod
    def categories(cls) -> tuple[str, ...]:
        return tuple(cls.QUESTIONS.keys())

    @staticmethod
    def _difficulty(question: Dict[str, Any]) -> str:
        """Resolve explicit or deterministic difficulty metadata."""
        raw = normalize_turkish(question.get("difficulty"))
        if raw in {"kolay", "easy"}:
            return "kolay"
        if raw in {"zor", "hard"}:
            return "zor"
        if raw in {"uzman", "expert"}:
            return "uzman"
        complexity = len(str(question.get("q") or "")) + len(str(question.get("a") or ""))
        return "kolay" if complexity < 65 else "zor" if complexity > 125 else "orta"

    @staticmethod
    def _question_id(category: str, question: Dict[str, Any]) -> str:
        payload = "|".join(
            (category, normalize_turkish(question.get("q")), normalize_turkish(question.get("a")))
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def get_question(
        self,
        category: str,
        *,
        scope: str = "global",
        difficulty: object | None = None,
    ) -> Dict[str, Any]:
        """Get a safe question with shuffled, deduplicated options.

        ``scope`` prevents repeats in a chat/session while preserving the old
        one-argument API. If a legacy bank has no exact difficulty bucket, the
        category is retained and the closest available question is selected.
        """
        cat_key = self.normalize_category(category)
        if cat_key not in self.QUESTIONS:
            cat_key = self._rng.choice(list(self.QUESTIONS.keys()))

        questions = [item for item in self.QUESTIONS[cat_key] if isinstance(item, dict)]
        requested = normalize_turkish(difficulty) if difficulty is not None else ""
        difficulty_map = {
            "easy": "kolay", "kolay": "kolay", "normal": "orta", "orta": "orta",
            "medium": "orta", "hard": "zor", "zor": "zor", "expert": "uzman", "uzman": "uzman",
        }
        requested = difficulty_map.get(requested, requested)
        if requested:
            filtered = [item for item in questions if self._difficulty(item) == requested]
            if filtered:
                questions = filtered

        scope_key = (str(scope), cat_key)
        used = self._used.setdefault(scope_key, set())
        available = [item for item in questions if self._question_id(cat_key, item) not in used]
        cycle_reset = not available
        if cycle_reset:
            used.clear()
            available = questions
        q = self._rng.choice(available)
        question_id = self._question_id(cat_key, q)
        used.add(question_id)

        answer = str(q.get("a") or "").strip()
        options: list[str] = []
        seen_options: set[str] = set()
        for option in [*(q.get("options") or ()), answer]:
            option_text = str(option or "").strip()
            option_key = normalize_turkish(option_text)
            if option_text and option_key and option_key not in seen_options:
                seen_options.add(option_key)
                options.append(option_text)
        options = options[:4]
        if normalize_turkish(answer) not in {normalize_turkish(item) for item in options}:
            options = [answer, *options[:3]]
        self._rng.shuffle(options)

        return {
            "question": q["q"],
            "answer": answer,
            "options": options,
            "category": cat_key,
            "question_id": question_id,
            "difficulty": self._difficulty(q),
            "cycle_reset": cycle_reset,
        }
