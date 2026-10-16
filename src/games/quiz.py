
import random
from typing import Dict, List, Optional, Any

class QuizGame:
    """Interactive quiz with multiple choice, scoring, and rich categories."""
    
    QUESTIONS = {
        "tip": [
            {"q": "Ä°nsan vÃ¼cudundaki en bÃ¼yÃ¼k organ hangisidir?", "a": "Deri", "options": ["Deri", "KaraciÄŸer", "AkciÄŸer", "BaÄŸÄ±rsak"]},
            {"q": "YetiÅŸkin bir insanda kaÃ§ adet kemik bulunur?", "a": "206", "options": ["206", "186", "256", "196"]},
            {"q": "KanÄ± pompalayan organ hangisidir?", "a": "Kalp", "options": ["Kalp", "AkciÄŸer", "BÃ¶brek", "Beyin"]},
            {"q": "Hangi kan grubu 'Genel Verici' olarak bilinir?", "a": "0 Negatif", "options": ["0 Negatif", "AB Pozitif", "A Pozitif", "B Negatif"]},
            {"q": "DNA'nÄ±n aÃ§Ä±lÄ±mÄ± nedir?", "a": "DeoksiribonÃ¼kleik Asit", "options": ["DeoksiribonÃ¼kleik Asit", "DiÅŸsel NÃ¼kleer Asit", "Diatomik Nitrogen Asit", "Deoksi NÃ¼kleer Amino"]},
            {"q": "Ä°nsan vÃ¼cudunda en kÃ¼Ã§Ã¼k kemik nerededir?", "a": "Kulak", "options": ["Kulak", "El", "Ayak", "Burun"]},
            {"q": "KÄ±rmÄ±zÄ± kan hÃ¼crelerinin Ã¶mrÃ¼ yaklaÅŸÄ±k kaÃ§ gÃ¼ndÃ¼r?", "a": "120", "options": ["120", "60", "30", "365"]},
            {"q": "DÃ¼nyadaki ilk kalp nakli hangi yÄ±l yapÄ±lmÄ±ÅŸtÄ±r?", "a": "1967", "options": ["1967", "1955", "1972", "1980"]},
            {"q": "Ä°nsÃ¼lin hangi organdan salgÄ±lanÄ±r?", "a": "Pankreas", "options": ["Pankreas", "KaraciÄŸer", "BÃ¶brek", "Dalak"]},
            {"q": "Ä°nsan beyninin yaklaÅŸÄ±k yÃ¼zde kaÃ§Ä± sudan oluÅŸur?", "a": "75", "options": ["75", "50", "90", "60"]},
        ],
        "tarih": [
            {"q": "Ä°stanbul hangi yÄ±l fethedildi?", "a": "1453", "options": ["1453", "1472", "1389", "1501"]},
            {"q": "TÃ¼rkiye Cumhuriyeti hangi yÄ±l kuruldu?", "a": "1923", "options": ["1923", "1920", "1919", "1925"]},
            {"q": "Ä°lk dÃ¼nya savaÅŸÄ± hangi yÄ±l baÅŸladÄ±?", "a": "1914", "options": ["1914", "1912", "1918", "1910"]},
            {"q": "Mustafa Kemal AtatÃ¼rk'Ã¼n doÄŸum yÄ±lÄ± nedir?", "a": "1881", "options": ["1881", "1878", "1885", "1880"]},
            {"q": "FransÄ±z Ä°htilali hangi yÄ±l gerÃ§ekleÅŸmiÅŸtir?", "a": "1789", "options": ["1789", "1776", "1799", "1750"]},
            {"q": "Malazgirt SavaÅŸÄ± hangi yÄ±l yapÄ±lmÄ±ÅŸtÄ±r?", "a": "1071", "options": ["1071", "1055", "1099", "1040"]},
            {"q": "OsmanlÄ± Ä°mparatorluÄŸu hangi yÄ±l kurulmuÅŸtur?", "a": "1299", "options": ["1299", "1280", "1320", "1350"]},
            {"q": "KurtuluÅŸ SavaÅŸÄ±'nÄ±n son muharebesi hangisidir?", "a": "BÃ¼yÃ¼k Taarruz", "options": ["BÃ¼yÃ¼k Taarruz", "Sakarya", "Ä°nÃ¶nÃ¼", "DumlupÄ±nar"]},
            {"q": "Berlin DuvarÄ± hangi yÄ±l yÄ±kÄ±lmÄ±ÅŸtÄ±r?", "a": "1989", "options": ["1989", "1991", "1985", "1990"]},
            {"q": "Magna Carta hangi yÄ±l imzalanmÄ±ÅŸtÄ±r?", "a": "1215", "options": ["1215", "1200", "1300", "1150"]},
        ],
        "cografya": [
            {"q": "DÃ¼nyanÄ±n en yÃ¼ksek daÄŸÄ± hangisidir?", "a": "Everest", "options": ["Everest", "K2", "Kangchenjunga", "AÄŸrÄ± DaÄŸÄ±"]},
            {"q": "TÃ¼rkiye'nin baÅŸkenti neresidir?", "a": "Ankara", "options": ["Ankara", "Ä°stanbul", "Ä°zmir", "Bursa"]},
            {"q": "En bÃ¼yÃ¼k okyanus hangisidir?", "a": "Pasifik", "options": ["Pasifik", "Atlantik", "Hint", "Arktik"]},
            {"q": "TÃ¼rkiye'nin en uzun nehri hangisidir?", "a": "KÄ±zÄ±lÄ±rmak", "options": ["KÄ±zÄ±lÄ±rmak", "FÄ±rat", "Sakarya", "Dicle"]},
            {"q": "DÃ¼nyanÄ±n en kÃ¼Ã§Ã¼k kÄ±tasÄ± hangisidir?", "a": "Avustralya", "options": ["Avustralya", "Avrupa", "Antarktika", "GÃ¼ney Amerika"]},
            {"q": "DÃ¼nyanÄ±n en derin gÃ¶lÃ¼ hangisidir?", "a": "Baykal GÃ¶lÃ¼", "options": ["Baykal GÃ¶lÃ¼", "Hazar Denizi", "Van GÃ¶lÃ¼", "Victoria GÃ¶lÃ¼"]},
            {"q": "Amazon Nehri hangi kÄ±tadadÄ±r?", "a": "GÃ¼ney Amerika", "options": ["GÃ¼ney Amerika", "Afrika", "Asya", "Kuzey Amerika"]},
            {"q": "Sahara Ã‡Ã¶lÃ¼ hangi kÄ±tadadÄ±r?", "a": "Afrika", "options": ["Afrika", "Asya", "Avustralya", "GÃ¼ney Amerika"]},
            {"q": "Karadeniz hangi boÄŸaz ile Akdeniz'e baÄŸlanÄ±r?", "a": "Ã‡anakkale", "options": ["Ã‡anakkale", "Ä°stanbul", "CebelitarÄ±k", "HÃ¼rmÃ¼z"]},
            {"q": "Japonya'nÄ±n baÅŸkenti neresidir?", "a": "Tokyo", "options": ["Tokyo", "Kyoto", "Osaka", "Seul"]},
        ],
        "spor": [
            {"q": "Futbol maÃ§larÄ± kaÃ§ dakika sÃ¼rer?", "a": "90", "options": ["90", "80", "120", "60"]},
            {"q": "Basketbolda bir takÄ±m sahada kaÃ§ kiÅŸiyle oynar?", "a": "5", "options": ["5", "6", "7", "4"]},
            {"q": "Olimpiyat halkalarÄ± kaÃ§ renkten oluÅŸur?", "a": "5", "options": ["5", "6", "4", "7"]},
            {"q": "DÃ¼nya KupasÄ± kaÃ§ yÄ±lda bir dÃ¼zenlenir?", "a": "4", "options": ["4", "2", "3", "5"]},
            {"q": "Teniste 'sÄ±fÄ±r' puanÄ± hangi terimle ifade edilir?", "a": "Love", "options": ["Love", "Zero", "Nil", "Blank"]},
            {"q": "FIFA DÃ¼nya KupasÄ±'nÄ± en Ã§ok kazanan Ã¼lke?", "a": "Brezilya", "options": ["Brezilya", "Almanya", "Ä°talya", "Arjantin"]},
            {"q": "Bir maraton kaÃ§ km'dir?", "a": "42.195", "options": ["42.195", "40", "50", "38.5"]},
            {"q": "Voleybolda bir takÄ±m kaÃ§ kiÅŸi ile sahada oynar?", "a": "6", "options": ["6", "5", "7", "8"]},
            {"q": "2022 DÃ¼nya KupasÄ± nerede yapÄ±lmÄ±ÅŸtÄ±r?", "a": "Katar", "options": ["Katar", "Rusya", "Brezilya", "Japonya"]},
            {"q": "Bir futbol takÄ±mÄ±nda kaleci hariÃ§ kaÃ§ oyuncu vardÄ±r?", "a": "10", "options": ["10", "9", "11", "8"]},
        ],
        "bilim": [
            {"q": "Suyun kaynama noktasÄ± kaÃ§ derecedir?", "a": "100", "options": ["100", "90", "110", "80"]},
            {"q": "Periyodik tablonun ilk elementi nedir?", "a": "Hidrojen", "options": ["Hidrojen", "Helyum", "Lityum", "Karbon"]},
            {"q": "YerÃ§ekimi kanununu kim bulmuÅŸtur?", "a": "Newton", "options": ["Newton", "Einstein", "Galileo", "Kepler"]},
            {"q": "GÃ¼neÅŸ sistemindeki en bÃ¼yÃ¼k gezegen hangisidir?", "a": "JÃ¼piter", "options": ["JÃ¼piter", "SatÃ¼rn", "UranÃ¼s", "NeptÃ¼n"]},
            {"q": "Albert Einstein'Ä±n en Ã¼nlÃ¼ formÃ¼lÃ¼ nedir?", "a": "E=mcÂ²", "options": ["E=mcÂ²", "F=ma", "PV=nRT", "V=IR"]},
            {"q": "IÅŸÄ±ÄŸÄ±n hÄ±zÄ± saniyede yaklaÅŸÄ±k kaÃ§ km'dir?", "a": "300.000", "options": ["300.000", "150.000", "500.000", "100.000"]},
            {"q": "DÃ¼nya'nÄ±n GÃ¼neÅŸ etrafÄ±ndaki dÃ¶nÃ¼ÅŸÃ¼ kaÃ§ gÃ¼n sÃ¼rer?", "a": "365", "options": ["365", "360", "370", "354"]},
            {"q": "Hangi element sembolÃ¼ 'O' dir?", "a": "Oksijen", "options": ["Oksijen", "Osmiyum", "Oganeson", "AltÄ±n"]},
            {"q": "Ay'a ilk ayak basan astronot kimdir?", "a": "Neil Armstrong", "options": ["Neil Armstrong", "Buzz Aldrin", "Yuri Gagarin", "John Glenn"]},
            {"q": "DNA'nÄ±n Ã§ift sarmal yapÄ±sÄ±nÄ± keÅŸfeden bilim insanlarÄ±?", "a": "Watson ve Crick", "options": ["Watson ve Crick", "Darwin ve Wallace", "Curie ve Pasteur", "Newton ve Leibniz"]},
        ],
        "sanat": [
            {"q": "Mona Lisa tablosunu kim yapmÄ±ÅŸtÄ±r?", "a": "Leonardo da Vinci", "options": ["Leonardo da Vinci", "Michelangelo", "Raphael", "Rembrandt"]},
            {"q": "YÄ±ldÄ±zlÄ± Gece tablosu kime aittir?", "a": "Van Gogh", "options": ["Van Gogh", "Monet", "Picasso", "DalÃ­"]},
            {"q": "Beethoven hangi mÃ¼zik dÃ¶neminin bestecisidir?", "a": "Romantik", "options": ["Romantik", "Barok", "Klasik", "Modern"]},
            {"q": "Hababam SÄ±nÄ±fÄ±'nÄ±n yazarÄ± kimdir?", "a": "RÄ±fat Ilgaz", "options": ["RÄ±fat Ilgaz", "Aziz Nesin", "Orhan Kemal", "YaÅŸar Kemal"]},
            {"q": "Guernica tablosunu kim yapmÄ±ÅŸtÄ±r?", "a": "Pablo Picasso", "options": ["Pablo Picasso", "Salvador DalÃ­", "Andy Warhol", "Claude Monet"]},
            {"q": "'Ã‡Ä±ÄŸlÄ±k' tablosu hangi sanatÃ§Ä±ya aittir?", "a": "Edvard Munch", "options": ["Edvard Munch", "Gustav Klimt", "Frida Kahlo", "RenÃ© Magritte"]},
        ],
        "yazilim": [
            {"q": "Python dilini kim geliÅŸtirmiÅŸtir?", "a": "Guido van Rossum", "options": ["Guido van Rossum", "Linus Torvalds", "James Gosling", "Dennis Ritchie"]},
            {"q": "HTML'nin aÃ§Ä±lÄ±mÄ± nedir?", "a": "HyperText Markup Language", "options": ["HyperText Markup Language", "High Tech Modern Language", "HyperText Machine Learning", "Home Tool Markup Language"]},
            {"q": "Linux Ã§ekirdeÄŸini kim yazmÄ±ÅŸtÄ±r?", "a": "Linus Torvalds", "options": ["Linus Torvalds", "Richard Stallman", "Ken Thompson", "Dennis Ritchie"]},
            {"q": "Git versiyon kontrol sistemini kim geliÅŸtirmiÅŸtir?", "a": "Linus Torvalds", "options": ["Linus Torvalds", "Guido van Rossum", "Bill Gates", "Mark Zuckerberg"]},
            {"q": "JavaScript hangi yÄ±l oluÅŸturulmuÅŸtur?", "a": "1995", "options": ["1995", "2000", "1990", "1998"]},
            {"q": "SQL ne anlama gelir?", "a": "Structured Query Language", "options": ["Structured Query Language", "Simple Question Language", "Standard Query Logic", "System Query Layer"]},
        ],
        "sinema": [
            {"q": "YÃ¼zÃ¼klerin Efendisi serisinin yÃ¶netmeni kimdir?", "a": "Peter Jackson", "options": ["Peter Jackson", "Steven Spielberg", "James Cameron", "Christopher Nolan"]},
            {"q": "Titanic filminin yÃ¶netmeni kimdir?", "a": "James Cameron", "options": ["James Cameron", "Ridley Scott", "Martin Scorsese", "Steven Spielberg"]},
            {"q": "Star Wars serisini yaratan kiÅŸi kimdir?", "a": "George Lucas", "options": ["George Lucas", "Steven Spielberg", "J.J. Abrams", "Ridley Scott"]},
            {"q": "Matrix filminin yÃ¶netmenleri kimlerdir?", "a": "Wachowski KardeÅŸler", "options": ["Wachowski KardeÅŸler", "Coen KardeÅŸler", "Russo KardeÅŸler", "Nolan KardeÅŸler"]},
            {"q": "Hangi film serisi 'Bir YÃ¼zÃ¼k hepsini yÃ¶netmek iÃ§in' sloganÄ±yla bilinir?", "a": "YÃ¼zÃ¼klerin Efendisi", "options": ["YÃ¼zÃ¼klerin Efendisi", "Harry Potter", "Hobbit", "Narnia"]},
            {"q": "Joker filminde baÅŸrolÃ¼ hangi oyuncu oynamÄ±ÅŸtÄ±r?", "a": "Joaquin Phoenix", "options": ["Joaquin Phoenix", "Heath Ledger", "Jack Nicholson", "Jared Leto"]},
        ],
        "edebiyat": [
            {"q": "SavaÅŸ ve BarÄ±ÅŸ romanÄ±nÄ±n yazarÄ± kimdir?", "a": "Tolstoy", "options": ["Tolstoy", "Dostoyevski", "Ã‡ehov", "PuÅŸkin"]},
            {"q": "SuÃ§ ve Ceza romanÄ±nÄ±n yazarÄ± kimdir?", "a": "Dostoyevski", "options": ["Dostoyevski", "Tolstoy", "Gogol", "Turgenyev"]},
            {"q": "KÃ¼Ã§Ã¼k Prens kitabÄ±nÄ±n yazarÄ± kimdir?", "a": "Antoine de Saint-ExupÃ©ry", "options": ["Antoine de Saint-ExupÃ©ry", "Victor Hugo", "Jules Verne", "Albert Camus"]},
            {"q": "Romeo ve Juliet kimin eseridir?", "a": "Shakespeare", "options": ["Shakespeare", "MoliÃ¨re", "Goethe", "Dante"]},
            {"q": "Nutuk kimin eseridir?", "a": "AtatÃ¼rk", "options": ["AtatÃ¼rk", "Ä°smet Ä°nÃ¶nÃ¼", "Mehmet Akif Ersoy", "Tevfik Fikret"]},
            {"q": "Sabahattin Ali'nin en Ã¼nlÃ¼ romanÄ± hangisidir?", "a": "KÃ¼rk Mantolu Madonna", "options": ["KÃ¼rk Mantolu Madonna", "Ä°Ã§imizdeki Åeytan", "KuyucaklÄ± Yusuf", "Sinekli Bakkal"]},
        ],
    }

    def get_question(self, category: str) -> Dict[str, Any]:
        """Get a random question with shuffled options."""
        cat_key = category.lower()
        if cat_key not in self.QUESTIONS:
            cat_key = random.choice(list(self.QUESTIONS.keys()))
        
        q = random.choice(self.QUESTIONS[cat_key])
        
        # Shuffle options
        options = list(q.get("options", [q["a"]]))
        random.shuffle(options)
        
        return {
            "question": q["q"],
            "answer": q["a"],
            "options": options,
            "category": cat_key
        }
