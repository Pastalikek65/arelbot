# Kurt Adam / Vampir Köylü

**GreyWolf kuralları, Türkçe rol kartları ve Arel’in kalıcı Telegram maç altyapısı.** 5–35 kişilik gruplarda, forum konusuna özel oynanır. Kaynak kod ve lisans bilgisi rol kartındaki özel sohbet düğmesinden açılır.

## Maça başlama

1. Grupta `/kurtadam` veya `/vampir` yaz. `/vampir normal`, `/vampir kaos` ve `/vampir klasik` ile türü doğrudan seçebilirsin. Klasik dört rollü varyant 7–20 kişiliktir.
2. Oyuncular Katıl düğmesine basar ve **Özel sohbeti hazırla** bağlantısıyla botla özel sohbet açar. Kurucu Normal/Kaos, rol ve oyun ayarlarını lobide seçer.
3. 5–35 oyuncu hazır olunca kurucu **Maçı başlat** der. Gizli kartlar tek tek botun özel sohbetinde gelir. Herkes rolünü onaylayınca ilk gece başlar.

## Tur akışı

| Aşama | Süre | Oyuncu eylemi |
|---|---:|---|
| İlk gece | 120 sn | Rol modeli, âşıklar ve ilk gece yetenekleri |
| Sonraki geceler | 90 sn | Sürü, koruma, bilgi, dönüşüm ve özel yetenekler |
| Gündüz | 90 sn | Grupta tartışma; gündüz yeteneği olanlar özelden seçim yapar |
| Gizli oy | 60 sn | Yaşayanlar bir hedef veya pas seçer; eşitlikte yalnız eşit adaylarla rövanş |
| Rövanş | 30 sn | Eşitlik sürerse kimse elenmez |
| Avcının son atışı | 30 sn | Elenen Avcıya özelden son hedef veya pas seçimi verilir |

Süreler panel ve özel kontroller teslim edilince başlar. Eksik hamle süre sonunda pas sayılır. Kullanılmış düğmeler ve eski maç kimliği reddedilir. Özel kararlar, takım arkadaşları, rol modeli ve sonuç notları gruba yazılmaz.

## Mod ve kazanma

Normal mod, kaynaktaki rol güçleri ve takım sınırlarıyla dengeli dağıtım arar. Kaos modu güç farkına izin verir; her iki mod da oynanabilir iki takım ve taraf çoğunluğu şartını korur. Kurucu bazı rolleri lobide açıp kapatabilir. Balkabağı rolü mevsimseldir.

Kurtlar, köylüler, tarikat ve bağımsız roller farklı koşullarda kazanır. Darağacı Yolcusu yalnız linç edilirse kazanır; âşıklar son iki kişi kalırsa birlikte kazanır. Kâhin gördükleri gizlidir; Sahte Kâhin kendi kartını Kâhin gibi görür. Kurt saldırısında Lanetli dönüşür; Alfa ısırığı bir sonraki gecede etkisini gösterir.

Kazananların puan ve coin kaydı işlem kimlikleriyle tekilleştirilir. Oyuncunun oyunu terk etmesi veya maçın iptali ödül doğurmaz. Maç en fazla 24 güne uzar. `/vampir durum` güncel paneli geri getirir; bitmiş maçtan yeniden başlatma önceki mod ve rol tercihlerini korur.

## Roller

| Rol | Taraf | Davranış |
|---|---|---|
| 👱 Köylü | Köy | Pasif/özel koşul |
| 🍻 Sarhoş | Köy | Pasif/özel koşul |
| 🌙 Gezgin | Köy | Gece birini ziyaret eder |
| 🔮 Kâhin | Köy | Bir oyuncunun rolünü öğrenir |
| 🎭 Hain | Köy | Son kurt ölürse Kurt olur |
| 👼 Koruyucu Melek | Köy | Bir oyuncuyu korur |
| 🕵 Dedektif | Köy | Şüpheliyi araştırır |
| 🐺 Kurt Adam | Kurt | Sürüyle av seçer |
| 😾 Lanetli | Köy | Kurt saldırısında Kurt olur |
| 🔫 Silahşor | Köy | Sınırlı mermiyle ateş eder |
| 👺 Darağacı Yolcusu | Bağımsız | Pasif/özel koşul |
| 🃏 Sahte Kâhin | Köy | Yanıltıcı görümler alır |
| 👶 Yabani Çocuk | Köy | Bir rol modeli seçer |
| 👁 Gözcü | Köy | Gerçek Kâhini özelden tanır |
| 🙇 Kâhin Çırağı | Köy | Pasif/özel koşul |
| 👤 Tarikatçı | Tarikat | Yeni üye kazanmaya çalışır |
| 💂 Tarikat Avcısı | Köy | Tarikatçıları bulur |
| 👷 Mason | Köy | Diğer Masonları özelden tanır |
| 🎭 Suret Değiştiren | Değişken | Bir rol modeli seçer |
| 🏹 Aşk Tanrısı | Köy | İki âşığı belirler |
| 🎯 Avcı | Köy | Öldükten sonra son atış |
| 🔪 Seri Katil | Bağımsız | Geceleri hedef seçer |
| 🔮 Büyücü | Kurt | Gece bilgi toplar |
| ⚡️ Alfa Kurt | Kurt | Sürüyle av seçer |
| 🐶 Kurt Yavrusu | Kurt | Sürüyle av seçer |
| ⚒ Demirci | Köy | Gümüş etkisi hazırlar |
| 🤕 Sakar | Köy | Pasif/özel koşul |
| 🎖 Belediye Başkanı | Köy | Kimliğini açığa çıkarır |
| 👑 Prens | Köy | Pasif/özel koşul |
| 🐺🌝 Gizli Kurt | Kurt | Kâhin tarafından Köylü görünür |
| ☮️ Barışçı | Köy | Oylamayı bir günlüğüne durdurur |
| 📚 Bilge | Köy | İlk kurt saldırısında hayatta kalır |
| 🌀 Orakl | Köy | Bir rol olasılığını eler |
| 💤 Uyku Perisi | Köy | Bir geceyi uykuya bırakır |
| 👱🌚 Kurt Görünümlü | Köy | Kâhin tarafından Kurt görünür |
| 😈 Hırsız | Değişken | Başka bir rolü çalabilir |
| 🤯 Olay Çıkaran | Köy | Aynı gün ikinci eleme açar |
| 👨‍🔬 Kimyacı | Köy | İksir kullanır |
| 🐺☃️ Kar Kurdu | Kurt | Gece hedefini dondurur |
| ☠️ Mezarcı | Köy | Pasif/özel koşul |
| 🦅 Kehanetçi | Köy | Oyunda bulunmayan rollere dair gece bilgisi |
| 🔥 Kundakçı | Bağımsız | Hedefi işaretler ve yakar |
| 🎃 Balkabağı | Köy | Mevsimsel; gündüz patlama girişimi |
| 🍚 Aşçı | Köy | Ziyaret izlerini takip eder |
| 🍸 Barmen | Köy | Bar ziyaretleri ve üç ziyarette dönüşüm |

Bu tablo kısa yönlendirmedir. Her rolün tam Türkçe açıklaması botta **Roller ve seçimler** panelinde bulunur. Rol açıklamalarının kaynağı [GreyWolfDev/Werewolf](https://github.com/GreyWolfDev/Werewolf/tree/bac172a29f8b394ebbb4822c6f244d769fac21ac); sürüm ve byte hashleri [kaynak kaydında](../src/vendor/greywolf/UPSTREAM.json).

## Doğrulama kapsamı

Oyun, birim/regresyon ve sentetik tam maçlarla doğrulanır. Gönüllü oyuncularla gerçek bir 5+ kişilik Telegram maçının tamamlanması ayrı bir kabul adımıdır; yapılmadıysa `NOT_RUN` olarak kalır. Eski dört rollü maçlar kural seçimi olmadan sürdürülür. Teknik durum [entegrasyon planında](WEREWOLF_INTEGRATION_PLAN.md) izlenir.
