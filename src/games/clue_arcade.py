"""Original Turkish clue and emoji puzzle rules; no Telegram or storage dependencies."""
from __future__ import annotations

import random
import secrets
import time
import unicodedata
from copy import deepcopy

BES_IPUCU = "bes_ipucu"
EMOJI_BILMECE = "emoji_bilmece"
GAME_KEYS = (BES_IPUCU, EMOJI_BILMECE)
TERMINAL = {"finished", "cancelled"}
PANEL_WAIT_SECONDS = 300
LOBBY_SECONDS = 1200
CLUE_SECONDS = 25
EMOJI_SECONDS = 55
REVEAL_SECONDS = 8
ROUNDS = 5
CLUE_POINTS = (10, 8, 6, 4, 2)

# Each clue moves from broad category to a distinctive fact; the fifth is decisive.
CLUES = (
    ("pusula", "Yön bulmaya yararım.", "Yolculuklarda kullanılırım.", "Kuzeyi gösteririm.", "İçimde mıknatıslı bir ibre vardır.", "Haritayla birlikte yön tayin ederim."),
    ("termos", "Bir kap türüyüm.", "Yolculuklarda yanına alınırım.", "İçeceğin sıcaklığını uzun süre korurum.", "Çift katmanlı yalıtımım vardır.", "Sıcak çay veya soğuk su taşırım."),
    ("uçurtma", "Açık havada eğlence sağlarım.", "Hafif malzemelerden yapılırım.", "Rüzgâr güç verince yükselirim.", "Bir ip beni elde tutar.", "Gökyüzünde süzülen renkli oyuncak benim."),
    ("deniz feneri", "Kıyıyla ilişkilidirim.", "Yüksek bir yapı olabilirim.", "Geceleri uzaktan görülürüm.", "Gemilere tehlikeli kıyıları gösteririm.", "Tepemde güçlü bir ışık döner."),
    ("kum saati", "Zamanla ilgilenirim.", "Elektriğe ihtiyaç duymam.", "İki haznem vardır.", "Taneler dar boğazdan aşağı akar.", "Ters çevirince süreyi yeniden ölçerim."),
    ("gökkuşağı", "Doğada görünürüm.", "Işıkla ilgim vardır.", "Yağmurdan sonra belirebilirim.", "Gökyüzünde renkli bir yay oluştururum.", "Güneş ışığının damlalarda ayrışmasıyla oluşurum."),
    ("büyüteç", "Görmeyi kolaylaştırırım.", "Cam bir parçam vardır.", "Küçük yazılar için kullanılırım.", "Nesneleri olduğundan büyük gösteririm.", "Saplı merceğimle ayrıntı incelenir."),
    ("paraşüt", "Havada kullanılırım.", "Kumaştan yapılabilirim.", "Açıldığımda hava direncini artırırım.", "Yüksekten inişi yavaşlatırım.", "Atlayanın sırtındaki çantadan açılırım."),
    ("teleskop", "Gözlem yapmaya yararım.", "Mercek ya da ayna içeririm.", "Uzaktaki cisimleri yakın gösteririm.", "Gök bilimciler beni kullanır.", "Ay ve gezegenleri izlemek için bana bakarsın."),
    ("mıknatıs", "Görünmez bir etkim vardır.", "Bazı metalleri etkilerim.", "İki kutbum vardır.", "Demiri kendime çekerim.", "Pusulanın ibresini yönlendiririm."),
    ("termometre", "Ölçüm yaparım.", "Sıcak ve soğukla ilgilenirim.", "Bir değer gösteririm.", "Hava ya da vücut sıcaklığını ölçerim.", "Derece cinsinden sıcaklığı gösteririm."),
    ("asansör", "Binalarda bulunurum.", "Düğmeyle çağrılırım.", "İnsanları taşırım.", "Katlar arasında dikey hareket ederim.", "Merdiven kullanmadan üst kata çıkarırım."),
    ("fırın", "Mutfakta bulunurum.", "Isı üretirim.", "İçime tepsi konur.", "Ekmek ve kek pişiririm.", "Yemeği kapalı haznemde yüksek ısıda pişiririm."),
    ("şemsiye", "Dışarıda işe yararım.", "Açılıp kapanırım.", "Elimde bir sap vardır.", "Yağmurdan korunmayı sağlarım.", "Başının üstünde taşınan katlanır örtüyüm."),
    ("daktilo", "Yazıyla ilgilidirim.", "Tuşlarım vardır.", "Kâğıt kullanırım.", "Harfleri mekanik vuruşlarla basarım.", "Bilgisayar öncesinin yazı makinesiyim."),
    ("bisiklet", "Ulaşım aracıyım.", "Motor gerektirmem.", "Dengede durmak gerekir.", "İki tekerleğim vardır.", "Pedal çevirerek ilerlersin."),
    ("fotoğraf makinesi", "Görüntüyle ilgilenirim.", "Bir düğmeye basılır.", "Objektifim vardır.", "Anları kare olarak kaydederim.", "Deklanşöre basınca fotoğraf çekerim."),
    ("satranç", "İki kişiyle oynanırım.", "Siyah ve beyaz vardır.", "Hamleler sırayla yapılır.", "Şah, vezir ve piyonlarım vardır.", "Şah mat ile sona ererim."),
    ("palet", "Boyayla ilişkilidirim.", "Düz bir yüzeyim vardır.", "Renkler üzerimde yan yana durur.", "Ressam fırçasını üzerimdeki boyaya batırır.", "Ressamın boyalarını karıştırdığı levhayım."),
    ("metronom", "Ritimle ilgilenirim.", "Düzenli aralıklarla ses çıkarırım.", "Müzisyenler beni kullanır.", "Çalma temposunu tutarım.", "Dakikadaki vuruş sayısını ayarlarım."),
    ("postacı", "Bir mesleğim vardır.", "Adresleri takip ederim.", "Sokak sokak gezerim.", "Mektup ve gönderi dağıtırım.", "Postaneden gelen zarfı kapına getiririm."),
    ("kütüphane", "Sessizlikle anılırım.", "Raflarım vardır.", "İnsanlar bende araştırma yapar.", "Ödünç kitap alınabilir.", "Kitapların düzenli toplandığı mekânım."),
    ("köprü", "İki tarafı birleştiririm.", "Üzerimden geçilir.", "Taş, çelik ya da beton olabilirim.", "Nehir veya vadiyi aşmayı sağlarım.", "İki kıyı arasındaki geçidim."),
    ("çapa", "Denizcilikte kullanılırım.", "Ağırım.", "Zincirle bağlanabilirim.", "Denizin dibine bırakılırım.", "Teknenin yerinde kalmasını sağlarım."),
    ("mikroskop", "Gözlem aracıyım.", "Merceklerim vardır.", "Laboratuvarda kullanılırım.", "Çok küçük şeyleri görünür kılarım.", "Hücrelere bakmak için kullanılırım."),
    ("merak", "Bir duyguyum.", "Soru sormaya yöneltirim.", "Yeni şeyleri öğrenmeyi isterim.", "Bilmediğin bir konuyu araştırmaya başlatırım.", "İnsanı keşfetmeye iten öğrenme isteğiyim."),
    ("sabır", "Bir tutumum.", "Zamanla ilişkilidirim.", "Zor anlarda işe yararım.", "Sonucu beklerken acele etmeme becerisiyim.", "Acele etmeden bekleme ve dayanma gücüyüm."),
    ("mimar", "Bir meslek sahibiyim.", "Çizim yaparım.", "Yapılarla ilgilenirim.", "Binaların kullanımını ve biçimini tasarlarım.", "Bir binanın projesini hazırlayan kişiyim."),
    ("arkeolog", "Bir araştırmacıyım.", "Geçmişi incelerim.", "Toprak altında çalışabilirim.", "Eski uygarlıkların kalıntılarını kazarım.", "Kazıda çıkan tarihî eserleri inceleyen kişiyim."),
    ("hakem", "Bir görevliyim.", "Kuralları takip ederim.", "Spor müsabakalarında bulunurum.", "Düdük çalıp karar verebilirim.", "Maçın kurallara uygun oynanmasını denetlerim."),
    ("çevirmen", "Bir meslek sahibiyim.", "Dillerle çalışırım.", "Anlamı korumam gerekir.", "Metni bir dilden diğerine aktarırım.", "Bir dilde söyleneni başka dilde ifade eden kişiyim."),
    ("gölge", "Işıkla ilişkiliyim.", "Biçimim değişebilir.", "Güneşli günde beni görebilirsin.", "Işık bir cisimle kesilince oluşurum.", "Yerde ya da duvarda görülen karanlık siluettim."),
    ("yankı", "Sesle ilişkiliyim.", "Biraz gecikerek duyulurum.", "Dağlık yerde belirgin olabilirim.", "Ses bir yüzeye çarpıp geri döner.", "Bağırınca geri işittiğin sestim."),
    ("takvim", "Zamanı düzenlerim.", "Sayfalı ya da dijital olabilirim.", "Günleri sıralarım.", "Ayları ve tarihleri gösteririm.", "Randevunun hangi gün olduğunu bende bulursun."),
    ("şifre", "Bir diziyim.", "Gizlilikle ilgilenirim.", "Harf ve rakam içerebilirim.", "Hesaba girişte beni yazarsın.", "Yetkisiz kişilerin erişimini engelleyen gizli bilgiyim."),
    ("çömlek", "Evde kullanılabilirim.", "Topraktan yapılırım.", "Bir çark üzerinde biçim alabilirim.", "Fırında pişirilince sertleşirim.", "Kilden yapılan geleneksel pişirme kabıyım."),
    ("kavanoz", "Bir kap türüyüm.", "Mutfakta sık görülürüm.", "Ağzım kapakla kapanır.", "İçimde reçel veya turşu saklanır.", "Camdan yapılmış, kapaklı saklama kabıyım."),
    ("kar küresi", "Bir süs eşyasıyım.", "Camdan bir bölümüm vardır.", "İçimde küçük bir manzara bulunur.", "Sallanınca beyaz tanelerim yağar.", "Salladığında içinde kar yağan cam süsüm."),
    ("salıncak", "Açık havada bulunabilirim.", "Oturmak için bir yerim vardır.", "İleri geri hareket ederim.", "Parkta zincirlerle asılırım.", "Çocukların üzerinde sallandığı oyun aracıyım."),
    ("vantilatör", "Evde işe yararım.", "Elektrikle çalışabilirim.", "Havayı hareket ettiririm.", "Pervanem döner.", "Sıcak günlerde rüzgâr üfleyerek serinletirim."),
    ("kroki", "Bir çizimim.", "Bir yeri anlatırım.", "Ölçekli olmam gerekmez.", "Sokakları ve dönemeçleri basitçe gösteririm.", "Bir adresi bulmak için çizilen basit yol haritasıyım."),
    ("mendil", "Küçük bir eşyayım.", "Cepte taşınabilirim.", "Kumaş veya kâğıttan yapılırım.", "Gözyaşı ya da burnu silmek için kullanılırım.", "Kullanıp atılabilen kâğıt türüm de vardır."),
    ("terazi", "Ölçüm yaparım.", "Pazarda kullanılabilirim.", "Kefelerim ya da dijital ekranım olabilir.", "Bir nesnenin ağırlığını bulurum.", "Tartı yapmaya yarayan aracım."),
    ("çalar saat", "Zamanla ilgilenirim.", "Yatağın yanında durabilirim.", "İstenilen ana ayarlanırım.", "Sabah ses çıkarıp uyandırırım.", "Alarmı kurulan saatim."),
    ("buz pateni", "Bir sporum.", "Denge gerektiririm.", "Özel ayakkabıyla yapılırım.", "Kaygan bir pistte ilerlenir.", "Bıçağı olan ayakkabıyla buz üzerinde kayılırım."),
    ("hamak", "Dinlenmek için kullanılırım.", "İki uçtan asılırım.", "Ağaçların arasında kurulabilirim.", "İçimde yatınca hafifçe sallanırsın.", "İplerle asılan kumaş yatağım."),
    ("zımba", "Masa üzerinde durabilirim.", "Ofiste işe yararım.", "Küçük metal parçalar kullanırım.", "Bastırınca kâğıtları birbirine tuttururum.", "Kâğıt destesini telle birleştiren aracım."),
    ("güneş kremi", "Bir bakım ürünüyüm.", "Cilde sürülürüm.", "Yazın sık kullanılırım.", "Üzerimde koruma faktörü yazar.", "Güneş ışınlarına karşı deriyi koruyan kremim."),
    ("deniz kabuğu", "Doğada bulunurum.", "Kumsalda toplanabilirim.", "Sert bir dış yüzeyim vardır.", "Bir deniz canlısının koruyucu evi olabilirim.", "Dalgaların kıyıya taşıdığı kabuğum."),
    ("piknik", "Açık havada yapılırım.", "Arkadaşlarla keyifliyimdir.", "Bir örtü serilebilir.", "Yiyecekler sepetle getirilir.", "Parkta veya kırda yenilen dışarı öğünüyüm."),
    ("yelpaze", "Elde taşınırım.", "Hafif bir eşyayım.", "Açılıp kapanabilirim.", "Sallandıkça yüzüne hava üflerim.", "Elektriksiz serinleten katlanır el aracıyım."),
    ("kiremit", "Bir yapı malzemesiyim.", "Sert ve ağır olabilirim.", "Yağmurdan korumaya yardım ederim.", "Çatıda yan yana dizilirim.", "Kırmızımsı çatı örtüsü parçasıyım."),
    ("havan", "Mutfakta kullanılabilirim.", "İki parçalı bir aracım.", "Sert malzemeden yapılırım.", "İçimde baharat tokmakla ezilir.", "Sarımsak dövmeye yarayan çukur kabım."),
    ("atlıkarınca", "Eğlence yerinde bulunurum.", "Çocuklar bana biner.", "Daire çizerek dönerim.", "Üzerimde yukarı aşağı giden at figürleri vardır.", "Lunaparktaki dönen atlı oyuncağım."),
    ("yağmurluk", "Dışarıda giyilirim.", "Kumaşım su geçirmez.", "Kapüşonum olabilir.", "Islanmamak için üzerime giyilirim.", "Yağışta giyilen koruyucu üstlüğüm."),
    ("dürüm", "Bir yiyeceğim.", "Elde yenebilirim.", "İçime çeşitli malzemeler konur.", "İnce ekmek sarılarak hazırlanırım.", "Lavaşın içine konan yemeğin sarılmış hâliyim."),
    ("fener alayı", "Bir toplu etkinliğim.", "Akşam düzenlenebilirim.", "İnsanlar birlikte yürür.", "Işıklar ellerde taşınır.", "Meşaleli veya ışıklı kutlama yürüyüşüyüm."),
    ("rüzgâr gülü", "Rüzgârla hareket ederim.", "Renkli olabilirim.", "Bir çubuğa takılırım.", "Kanatlarım esen havayla döner.", "Çocukların üfleyerek döndürdüğü pervaneli oyuncağım."),
    ("kumdan kale", "Sahilde yapılırım.", "Kalıcı değilim.", "Islak malzemeyle daha kolay biçim alırım.", "Kova ve kürekle kulelerim yapılır.", "Kumsalda kumla yapılan oyuncak kaleyim."),
    ("posta kutusu", "Bir kutuyum.", "Sokakta ya da binada bulunurum.", "Üzerimde bir giriş aralığı vardır.", "Mektuplar içime bırakılır.", "Gönderilecek zarfların atıldığı kutuyum."),
)

CATEGORIES = ("Nesne",) * 20 + ("Meslek", "Mekân", "Yapı", "Denizcilik", "Araç") + (
    "Duygu", "Tutum", "Meslek", "Meslek", "Görev", "Meslek", "Doğa", "Ses", "Nesne", "Kavram")
CATEGORIES += ("Nesne", "Kap", "Süs", "Oyun aracı", "Ev aleti", "Çizim", "Eşya",
               "Ölçüm aracı", "Saat", "Spor", "Dinlenme", "Ofis aracı", "Bakım ürünü",
               "Doğa", "Etkinlik", "Eşya", "Yapı malzemesi", "Mutfak aracı", "Eğlence",
               "Giysi", "Yiyecek", "Etkinlik", "Oyuncak", "Oyun", "Eşya")

# Emoji cards use familiar, concrete concepts; aliases are accepted after Turkish folding.
EMOJI = (
    ("☀️🕶️", "güneş gözlüğü", "gunes gozlugu"), ("🌧️☂️", "şemsiye", "semsiye"),
    ("🍎🥧", "elmalı turta", "elmali turta"), ("🐝🍯", "bal", "bal"),
    ("🌙🚶", "gece yürüyüşü", "gece yuruyusu"), ("🚲🏁", "bisiklet yarışı", "bisiklet yarisi"),
    ("📚🏫", "okul kütüphanesi", "okul kutuphanesi"), ("🎂🕯️", "doğum günü", "dogum gunu"),
    ("🐟🍞", "balık ekmek", "balik ekmek"), ("☕🧊", "buzlu kahve", "buzlu kahve"),
    ("🍋🥤", "limonata", "limonata"), ("🌽🍿", "patlamış mısır", "patlamis misir"),
    ("🥔🍟", "patates kızartması", "patates kizartmasi"), ("🍓🍰", "çilekli pasta", "cilekli pasta"),
    ("🥛🍫", "çikolatalı süt", "cikolatali sut"), ("🍅🥣", "domates çorbası", "domates corbasi"),
    ("🧀🥪", "peynirli sandviç", "peynirli sandvic"), ("🍯🥛", "ballı süt", "balli sut"),
    ("🍌🥛", "muzlu süt", "muzlu sut"), ("🥚🍳", "sahanda yumurta", "sahanda yumurta"),
    ("🌊🏄", "sörf", "sorf"), ("🏔️⛷️", "kayak", "kayak"),
    ("🌲⛺", "orman kampı", "orman kampi"), ("🌊🏊", "denizde yüzme", "denizde yuzme"),
    ("🧗🏔️", "dağ tırmanışı", "dag tirmanisi"), ("🛶🌊", "kano", "kano"),
    ("🎣🐟", "balık tutma", "balik tutma"), ("🪁💨", "uçurtma uçurma", "ucurtma ucurma"),
    ("🪴💧", "çiçek sulama", "cicek sulama"), ("🌱🪴", "fidan dikme", "fidan dikme"),
    ("🎨🖌️", "resim yapma", "resim yapma"), ("🎹🎶", "piyano çalma", "piyano calma"),
    ("🎸🎤", "şarkı söyleme", "sarki soyleme"), ("📷🌅", "gün batımı fotoğrafı", "gun batimi fotografi"),
    ("📖🛏️", "yatakta kitap okuma", "yatakta kitap okuma"), ("✉️📮", "mektup gönderme", "mektup gonderme"),
    ("🧩🧠", "yapboz", "yapboz"), ("♟️👑", "satranç", "satranc"),
    ("🎲👨‍👩‍👧", "aile oyunu", "aile oyunu"), ("🎬🍿", "sinema", "sinema"),
    ("🎭👏", "tiyatro", "tiyatro"), ("🎫🎶", "konser", "konser"),
    ("🚌🏫", "okul servisi", "okul servisi"), ("🚂🧳", "tren yolculuğu", "tren yolculugu"),
    ("✈️🧳", "uçak yolculuğu", "ucak yolculugu"), ("🚕🏙️", "şehir taksisi", "sehir taksisi"),
    ("🛳️🌊", "gemi yolculuğu", "gemi yolculugu"), ("🚦🚶", "yaya geçidi", "yaya gecidi"),
    ("🗺️🧭", "yön bulma", "yon bulma"), ("🏖️🏰", "kumdan kale", "kumdan kale"),
    ("⛄🥕", "kardan adam", "kardan adam"), ("🍂🧹", "yaprak süpürme", "yaprak supurme"),
    ("🌈🌧️", "gökkuşağı", "gokkusagi"), ("⚡🌩️", "şimşek", "simsek"),
    ("🌕🌙", "dolunay", "dolunay"), ("🐱🐟", "kedi maması", "kedi mamasi"),
    ("🐶🦴", "köpek kemiği", "kopek kemigi"), ("🐦🏠", "kuş yuvası", "kus yuvasi"),
    ("🐢🏁", "kaplumbağa yarışı", "kaplumbaga yarisi"), ("🦋🌸", "çiçekte kelebek", "cicekte kelebek"),
    ("🍞🧀", "peynirli tost", "peynirli tost"), ("🍚🥛", "sütlaç", "sutlac"),
    ("🍋🍰", "limonlu kek", "limonlu kek"), ("🍯🥞", "ballı krep", "balli krep"),
    ("🍅🍝", "domatesli makarna", "domatesli makarna"), ("🥒🫙", "salatalık turşusu", "salatalik tursusu"),
    ("🍉🧊", "soğuk karpuz", "soguk karpuz"), ("🧀🥧", "peynirli börek", "peynirli borek"),
    ("🐈🧶", "iple oynayan kedi", "iple oynayan kedi"), ("🐕🏡", "köpek kulübesi", "kopek kulubesi"),
    ("🐝🌸", "çiçekte arı", "cicekte ari"), ("🐣🥚", "yumurtadan çıkan civciv", "yumurtadan cikan civciv"),
    ("🦆🏞️", "göldeki ördek", "goldeki ordek"), ("🐜🍞", "ekmek taşıyan karınca", "ekmek tasiyan karinca"),
    ("🚴🏞️", "parkta bisiklet", "parkta bisiklet"), ("🏕️🔥", "kamp ateşi", "kamp atesi"),
    ("🛶🏞️", "gölde kano", "golde kano"), ("🏖️📖", "plajda kitap okuma", "plajda kitap okuma"),
    ("🌧️🥾", "yağmurda yürüyüş", "yagmurda yuruyus"), ("❄️🛷", "kızakla kayma", "kizakla kayma"),
    ("🧳🏨", "otel tatili", "otel tatili"), ("🚉🎫", "tren bileti", "tren bileti"),
    ("🚲🔒", "bisiklet kilidi", "bisiklet kilidi"), ("🚌🛑", "otobüs durağı", "otobus duragi"),
    ("🛫🌍", "yurt dışı uçuşu", "yurt disi ucusu"),
)

# Hand-reviewed alternatives for the displayed concept, not fuzzy matching.
EMOJI_ALIASES = {
    "güneş gözlüğü": ("gözlük",),
    "elmalı turta": ("elma turtası",),
    "gece yürüyüşü": ("gece yürüyüşü yapmak",),
    "bisiklet yarışı": ("bisiklet yarışması",),
    "okul kütüphanesi": ("kütüphane",),
    "doğum günü": ("doğum günü kutlaması",),
    "balık ekmek": ("balık ekmeği",),
    "patlamış mısır": ("patlamış mısır", "mısır patlağı"),
    "patates kızartması": ("kızarmış patates",),
    "çilekli pasta": ("çilek pastası",),
    "domates çorbası": ("domatesli çorba",),
    "sahanda yumurta": ("tavada yumurta",),
    "denizde yüzme": ("yüzme", "denizde yüzmek"),
    "dağ tırmanışı": ("dağa tırmanış",),
    "balık tutma": ("balık avlama",),
    "uçurtma uçurma": ("uçurtma uçurmak",),
    "çiçek sulama": ("çiçekleri sulama",),
    "fidan dikme": ("ağaç dikme",),
    "resim yapma": ("resim çizme",),
    "piyano çalma": ("piyano çalmak",),
    "şarkı söyleme": ("şarkı söylemek",),
    "gün batımı fotoğrafı": ("gün batımı",),
    "yatakta kitap okuma": ("kitap okuma",),
    "mektup gönderme": ("mektup yollama",),
    "okul servisi": ("servis aracı",),
    "tren yolculuğu": ("tren seyahati",),
    "uçak yolculuğu": ("uçak seyahati",),
    "gemi yolculuğu": ("gemi seyahati",),
    "yön bulma": ("yön tayini",),
    "kumdan kale": ("kum kalesi",),
    "kardan adam": ("kar adam",),
    "yaprak süpürme": ("yaprakları süpürme",),
    "köpek kemiği": ("köpek için kemik",),
    "kuş yuvası": ("kuşların yuvası",),
    "kaplumbağa yarışı": ("kaplumbağa yarışması",),
}


def emoji_category(index):
    if index >= 80:
        return "Ulaşım ve seyahat"
    if index >= 74:
        return "Açık hava"
    if index >= 68:
        return "Hayvanlar"
    if index >= 60:
        return "Yiyecek ve içecek"
    if index < 2:
        return "Günlük eşya"
    if index < 4:
        return "Yiyecek ve içecek"
    if index < 8:
        return "Etkinlik"
    if index < 20:
        return "Yiyecek ve içecek"
    if index < 30:
        return "Açık hava"
    if index < 42:
        return "Etkinlik"
    if index < 49:
        return "Ulaşım"
    if index == 49:
        return "Açık hava"
    if index < 55:
        return "Doğa"
    if index < 59:
        return "Hayvan ve doğa"
    return "Doğa"


def normalize(value):
    folded = str(value).strip().casefold().translate(str.maketrans("ıİşğüöç", "iisguoc"))
    folded = "".join(char for char in unicodedata.normalize("NFKD", folded)
                     if not unicodedata.combining(char))
    return " ".join("".join(char if char.isalnum() else " " for char in folded).split())


def card(game_key, index):
    if game_key == BES_IPUCU:
        row = CLUES[index]
        return {"id": index, "answer": row[0], "aliases": [normalize(row[0])],
                "category": CATEGORIES[index],
                "clues": list(row[1:])}
    row = EMOJI[index]
    words = normalize(row[1]).split()
    aliases = {normalize(row[1]), normalize(row[2])}
    aliases.update(normalize(alias) for alias in EMOJI_ALIASES.get(row[1], ()))
    return {"id": index, "emoji": row[0], "answer": row[1],
            "aliases": sorted(aliases), "category": emoji_category(index),
            "word_lengths": [len(word) for word in words]}


def public_state(state):
    result = deepcopy(state)
    if result["phase"] == "playing" and result.get("card"):
        result["card"].pop("answer", None)
        result["card"].pop("aliases", None)
        if result["game_key"] == BES_IPUCU:
            result["card"]["clues"] = result["card"]["clues"][:result["clue_no"]]
    result.pop("deck", None)
    result.pop("processed_actions", None)
    return result


def _copy(state, now):
    result = deepcopy(state)
    result["version"] += 1
    result["last_activity_at"] = float(now)
    return result


def _participants(state):
    return [p for p in state["players"] if p.get("active", True)]


def _player(state, user_id):
    return next((p for p in state["players"] if p["id"] == int(user_id) and p.get("active", True)), None)


def new(*, game_key, chat_id, thread_id, owner_id, owner_name, round_id,
        now=None, seed=None, prior=None, solo=False):
    if game_key not in GAME_KEYS:
        raise ValueError("ca_invalid_game")
    moment = time.time() if now is None else float(now)
    pool = len(CLUES if game_key == BES_IPUCU else EMOJI)
    deck = list(prior.get("deck", [])) if prior else []
    cursor = int(prior.get("deck_cursor", 0)) if prior else 0
    if not deck or len(deck) != pool:
        deck = list(range(pool))
        random.Random(seed if seed is not None else secrets.randbits(64)).shuffle(deck)
        cursor = 0
    state = {"game_key": game_key, "chat_id": int(chat_id), "thread_id": thread_id,
             "owner_id": int(owner_id), "round_id": str(round_id), "version": 0,
             "phase": "lobby", "phase_seq": 0, "round_no": 0,
             "players": [{"id": int(owner_id), "name": str(owner_name)[:32], "score": 0, "active": True}],
             "deck": deck, "deck_cursor": cursor, "card": None, "clue_no": 0,
             "attempts": {}, "solved_ids": [], "round_winner": None,
             "created_at": moment, "last_activity_at": moment,
             "lobby_expires_at": moment + LOBBY_SECONDS, "deadline": None,
             "panel_wait_until": None, "panel_dirty": True, "delivery_retry_at": 0,
             "panel_message_id": None, "input_floor": 0, "input_opened_at": None,
             "score_pending": False, "winner_ids": [], "reason": None,
             "processed_actions": [], "solo": bool(solo)}
    if solo:
        start(state, user_id=owner_id, now=moment, in_place=True)
    return state


def _next_round(state, now):
    if state["round_no"] >= ROUNDS or not _participants(state):
        active = _participants(state)
        maximum = max((p["score"] for p in active), default=0)
        state.update(phase="finished", phase_seq=state["phase_seq"] + 1,
                     deadline=None, panel_wait_until=None, lobby_expires_at=None,
                     card=None, winner_ids=[p["id"] for p in active if maximum > 0 and p["score"] == maximum],
                     score_pending=any(p["score"] > 0 for p in active))
        return
    pool = len(CLUES if state["game_key"] == BES_IPUCU else EMOJI)
    if state["deck_cursor"] >= pool:
        last = state["deck"][-1]
        seed = secrets.randbits(64)
        fresh = list(range(pool))
        random.Random(seed).shuffle(fresh)
        if fresh[0] == last:
            fresh[0], fresh[1] = fresh[1], fresh[0]
        state["deck"], state["deck_cursor"] = fresh, 0
    index = state["deck"][state["deck_cursor"]]
    state["deck_cursor"] += 1
    state.update(phase="playing", phase_seq=state["phase_seq"] + 1,
                 round_no=state["round_no"] + 1, card=card(state["game_key"], index),
                 clue_no=1 if state["game_key"] == BES_IPUCU else 0,
                 attempts={}, solved_ids=[], round_winner=None,
                 deadline=None, panel_wait_until=float(now) + PANEL_WAIT_SECONDS,
                 input_opened_at=None)


def join(state, *, user_id, user_name, now=None):
    moment = time.time() if now is None else float(now)
    if state["phase"] not in {"lobby", "playing"}:
        raise ValueError("ca_wrong_phase")
    if _player(state, user_id):
        raise ValueError("ca_already_joined")
    if len(_participants(state)) >= 20:
        raise ValueError("ca_full")
    result = _copy(state, moment)
    prior = next((p for p in result["players"] if p["id"] == int(user_id)), None)
    if prior:
        prior.update(active=True, name=str(user_name)[:32])
    else:
        result["players"].append({"id": int(user_id), "name": str(user_name)[:32],
                                  "score": 0, "active": True})
    return result


def start(state, *, user_id, now=None, in_place=False):
    moment = time.time() if now is None else float(now)
    if state["phase"] != "lobby" or moment >= state["lobby_expires_at"]:
        raise ValueError("ca_lobby_closed")
    if int(user_id) != state["owner_id"]:
        raise ValueError("ca_not_owner")
    result = state if in_place else _copy(state, moment)
    result["lobby_expires_at"] = None
    _next_round(result, moment)
    return result


def leave(state, *, user_id, now=None):
    if state["phase"] in TERMINAL or not _player(state, user_id):
        raise ValueError("ca_not_player")
    moment = time.time() if now is None else float(now)
    result = _copy(state, moment)
    _player(result, user_id)["active"] = False
    if not _participants(result):
        return cancel(result, user_id=result["owner_id"], now=moment,
                      reason="all_players_left", in_place=True)
    if int(user_id) == result["owner_id"]:
        result["owner_id"] = _participants(result)[0]["id"]
    return result


def cancel(state, *, user_id, moderator=False, now=None, reason="owner", in_place=False):
    if state["phase"] in TERMINAL:
        raise ValueError("ca_wrong_phase")
    if int(user_id) != state["owner_id"] and not moderator:
        raise ValueError("ca_not_owner")
    moment = time.time() if now is None else float(now)
    result = state if in_place else _copy(state, moment)
    result.update(phase="cancelled", phase_seq=result["phase_seq"] + 1,
                  deadline=None, panel_wait_until=None, lobby_expires_at=None,
                  card=None, score_pending=False, winner_ids=[], reason=reason)
    return result


def guess(state, *, user_id, user_name, text, now=None):
    moment = time.time() if now is None else float(now)
    if state["phase"] != "playing" or state["deadline"] is None or moment >= state["deadline"]:
        raise ValueError("ca_wrong_phase")
    normalized = normalize(text)
    if not normalized or len(normalized) > 64 or len(str(text)) > 100:
        raise ValueError("ca_invalid_guess")
    result = _copy(state, moment)
    if not _player(result, user_id):
        # Membership is explicit in both domain and shared text routing.
        # Players can join from the live panel without resetting their rights.
        raise ValueError("ca_not_player")
    uid = str(int(user_id))
    used = result["attempts"].get(uid, 0)
    allowance = 1 if result["game_key"] == BES_IPUCU else 2
    if used >= allowance:
        return state
    result["attempts"][uid] = used + 1
    if normalized in result["card"]["aliases"]:
        points = CLUE_POINTS[result["clue_no"] - 1] if result["game_key"] == BES_IPUCU else (6 if used == 0 else 3)
        _player(result, user_id)["score"] += points
        result["round_winner"] = int(user_id)
        result["solved_ids"].append(int(user_id))
        _reveal(result, moment)
    elif (result["game_key"] == BES_IPUCU and
          all(result["attempts"].get(str(p["id"]), 0) >= 1 for p in _participants(result))):
        _step_or_reveal(result, moment)
    return result


def pass_clue(state, *, user_id, now=None):
    moment = time.time() if now is None else float(now)
    if (state["game_key"] != BES_IPUCU or state["phase"] != "playing"
            or state["deadline"] is None or moment >= state["deadline"]):
        raise ValueError("ca_wrong_phase")
    if not _player(state, user_id):
        raise ValueError("ca_not_player")
    uid = str(int(user_id))
    if state["attempts"].get(uid, 0):
        raise ValueError("ca_no_attempts")
    result = _copy(state, moment)
    result["attempts"][uid] = 1
    if all(result["attempts"].get(str(p["id"]), 0) >= 1 for p in _participants(result)):
        _step_or_reveal(result, moment)
    return result


def _step_or_reveal(state, now):
    if state["clue_no"] < 5:
        state.update(clue_no=state["clue_no"] + 1, attempts={},
                     phase_seq=state["phase_seq"] + 1, deadline=None,
                     panel_wait_until=float(now) + PANEL_WAIT_SECONDS)
    else:
        _reveal(state, now)


def _reveal(state, now):
    state.update(phase="reveal", phase_seq=state["phase_seq"] + 1,
                 deadline=None, panel_wait_until=float(now) + PANEL_WAIT_SECONDS)


def advance(state, *, now=None):
    moment = time.time() if now is None else float(now)
    if state["phase"] in TERMINAL:
        return state
    if state["phase"] == "lobby":
        return cancel(state, user_id=state["owner_id"], now=moment,
                      reason="lobby_timeout") if moment >= state["lobby_expires_at"] else state
    if state["deadline"] is None:
        return cancel(state, user_id=state["owner_id"], now=moment,
                      reason="panel_timeout") if moment >= state["panel_wait_until"] else state
    if moment < state["deadline"]:
        return state
    result = _copy(state, moment)
    if state["phase"] == "reveal":
        _next_round(result, moment)
    elif state["game_key"] == BES_IPUCU:
        _step_or_reveal(result, moment)
    else:
        _reveal(result, moment)
    return result
