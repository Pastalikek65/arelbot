"""Original local word-game bank with Turkish letters preserved."""

from __future__ import annotations

import unicodedata
from pathlib import Path


def canonical_word(value: object) -> str:
    return unicodedata.normalize("NFC", str(value or "").strip()).translate(
        str.maketrans({"I": "ı", "İ": "i"})
    ).lower()


# Common nouns and dictionary forms; no personal names or generated spellings.
# This bank is authored for Arel and does not depend on a network dictionary.
ORIGINAL_WORDS = frozenset("""
aba abajur abla acı ada adam adres afiş ağaç ağıl ağız ahtapot aile akarsu
akıl akım akrep akşam alan alarm alay albüm alçı alev alfabe alıcı alın
alkış altın amaç ambar anahtar anı anlam anlatı anne anten arı araç aralık
arama armut arpa arsa arşiv aslan asker askı atık atlas avcı ayna ayran
ayva ayı ayak aygır ayraç azim baba baca badem bağ bahar bahçe bakır
baklava bal balık balkon balon balta bamya bardak barış basamak baskı baş
başak bavul baykuş bayrak bebek beden bekçi belge bellek benek berber
bere beşik bez bezelye bıçak biber bilek bilezik bilgi bilim bina bisküvi
bisiklet bitki bodrum bohça boru boya boyun bozkır böcek börek bölüm
bulgur bulut burun buz buzağı bülbül büyüteç cadde cam can ceviz ceylan
cilt cisim cümle çadır çağ çakıl çam çamaşır çanak çanta çapa çare çarşı
çatal çatı çay çayır çekiç çekirdek çekmece çember çene çerçeve çeşme
çevre çeyiz çiçek çiftçi çizgi çizme çivi çoban çocuk çorap çorba çöl
çözüm çuval dal dalga dam damak damla dans darı davul dayı defter değirmen
delik demir deneme denge deniz depo dere deri ders desen deve deney dikdörtgen
diken dil dilek dinleti dizi dizlik doğa doğan doktor dolap dolgu don donanım
dost dosya duman durak dut duvar düdük düğme düğün dünya düşünce ebru eczane
edebiyat efekt efsane egemenlik ekin ekmek ekran elbise elçi eldiven elek
elektrik elma elmas emek emanet emir endişe enerji engel enlem erik eser
etek etiket evrak evren eyer ezgi fabrika fener fındık fırça fırın fıstık
fidan fikir fil file film fincan fişek fotoğraf fren fular galeri garaj
gaz gazete gece gelincik gemi gençlik gezegen geyik gitar giysi gök gölge
göl göz gözenek gözlük gökkuşağı görev gram gül gümüş güneş güvercin haber
halat halı halka hamak hamur harf harita hasır havlu hava havuç hayal hece
hedef hediye hekim helva heykel hırka hız hobi horoz hücre ıhlamur ırmak
ısı ışık ısırgan ızgara ibik iğde iğne ilçe ilgi ilik ilke imza inat incir
inek insan iplik irmik iskele istasyon işaret itfaiye iz izci kabak kabin
kablo kabuk kadife kafes kağıt kahve kakao kalem kale kalıp kalp kamera
kamp kanat kanal kanca kanyon kapak kapı kaplan kaptan kar karar karanfil
karga karınca kart kartal kase kasırga kaş kaşık kat katman kavak kavun
kaya kayık kayısı kaymak kaz kazak kazma keçi kedi kek kekik kelime keman
kemer kemik kepçe kereviz kestane kelebek keten kıl kılıç kıyı kızak kibrit
kil kilim kilit kimlik kiraz kirpi kitap kivi klavye koala koku kol kolye
komşu konak konu koro koridor korkuluk korna koru kova koyun köfte kök kömür
köpek köprü köşe köy köylü kral krep kristal kuğu kulak kule kulübe kum
kumbara kumaş kumsal kunduz kurabiye kurbağa kurdele kuruş kuş kuşak kuyu
küp kürek kürk kütük lale lamba lastik lavabo leblebi leke leylek lif liman
limon liste lodos lokma lokum lüfer maden makara makarna makas makine mandal
mandalina manzara marul masa masal maske maya maydanoz mektup melek melodi
mendil mercimek merdiven meşe mevsim meydan meyve mezura mısır mıknatıs mil
mimar minder minik misafir model mola mor mozaik mum musluk mutfak müzik
nabız nakış nal nar nergis nehir nem nene nesne neşe nişan nitelik nohut
nota nöbet numara ocak oda odun oğul okul okyanus olta omuz opera orak
orkestra orman oyun oymak ördek örgü örnek örtü öykü paket palamut palet
palto pamuk papağan papatya para parça park parmak pas pasta patates paten
patika patlıcan pazar pelikan pencere perde pergel petek peynir piyano
pide pil pilav pilot pipet pirinç portakal posta pul pusula radyo raf
raket ray reçel rehber renk resim ritim robot roket roman rüzgâr saat
sabır sabun saç saçak saha sahil sakız saksı sal salata salça salıncak
salyangoz saman sanat sandal sandalye sandık sap saray sarma satır savcı
saz sebze sefer sel sepet sera serçe sergi ses set sevgi seyir sıfat
sığınak sır sıra sırt sıvı sicim silgi simit sinek sinema sincap sini
sistem siyaset soba sokak soluk somun sopa soğan soru söz spor su sucuk
sulak sultan sunucu sünger süpürge sürahi süt şair şal şamdan şapka şarkı
şeftali şehriye şehir şeker şelale şemsiye şerit şişe şube şut tabak tablo
tahta takım takvim tarak tarçın tarhana tarla taş tavşan tava tavuk tavan
tebeşir tekne tel telefon teleskop temizlik tencere tepe terazi terlik
terzi testi tezgâh tekerlek tepsi tilki timsah tiyatro tohum tokmak top
toprak torba toz tuğla tulum turna turp tuval tuz tüfek tünel uçak uçurtma
ufuk unvan usta uydu uyku uzman ülke ütü üzüm vadi vagon vakit vapur varil
vazo vergi veri vida vişne vitrin volkan yağ yağmur yaka yakıt yalıtım yanak
yapı yaprak yarış yas yasa yastık yaya yayın yaz yazı yazılım yemek yelken
yelpaze yem yemin yeni yer yılan yıl yıldırım yıldız yorgan yol yonca
yontu yudum yumak yumurta yunus yuva yüz yüzey yüzük zaman zarf zar zarif
zemin zencefil zeytin zılgıt zil zincir ziraat zürafa
""".split())


# The vetted, pinned Zemberek lemma snapshot is bundled with the image.  Keep
# the original Arel bank as a separate union so historical accepted words stay
# valid even when upstream represents a homograph as a proper name.
_LEXICON_PATH = Path(__file__).resolve().parents[2] / "assets" / "word_chain" / "lexicon.txt"
with _LEXICON_PATH.open(encoding="utf-8") as _lexicon_file:
    WORDS = ORIGINAL_WORDS | frozenset(line.strip() for line in _lexicon_file if line.strip())
