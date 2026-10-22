# Arel CLI hız ve kişilik kontrolü — 22 Ekim 2026

Kapsam: yerel Codex CLI köprüsü, gerçek GPT-5.6 Luna çağrıları. Her örnekte uygulamadaki PersonalityPolicy ve normal sohbet talimatları kullanıldı. Router fast zaman bütçesi uygulandı. Telegram teslim süresi, sunucu gecikmesi ve kalıcı kullanıcı ayarının Telegram üzerinden seçilmesi bu ölçümde sınanmadı.

## Sonuç

- Çağrı: 10; başarılı: 10.
- Ortalama tam yanıt süresi: 5.614 s.
- Medyan: 5.645 s; min: 4.709 s; max: 6.562 s.
- Bütün gerçek yanıtlar gpt-5.6-luna ile üretildi; 6 Luna yedeği kullanılmadı.
- Mevcut kişilik/stil/kontrol testleri: 32 passed.
- Normal moddan kullanıcı mesajıyla otomatik roast geçişi yapılmadı.
- Roast seçimi açık küfürlü taşlamaya yansıdı; kullanıcı şakayı durdurunca kısa, düz yanıt üretildi.
- Ciddi/dengeli/iğneleyici örneklerde üslup ayrımı zayıf. Tek örnekle bütün mod davranışı genellenemez.
- İki yanıtta dil tutarlılığı bozuldu: teknik örnekte Gujarati sözcük, son gündelik örnekte Çince sözcük görüldü.
- Gündelik iki cevapta aynı gülme kullanıldı; her vaka bağımsız geçmişle çalıştığından çok turlu tekrar önleme bu ölçümle doğrulanmadı.
- Sonuç: erişim ve temel kişilik aktarımı çalışıyor; Türkçe tutarlılığı ve mod ayrımı kalite açığıdır.

## Gerçek yanıtlar

### normal_greeting — 5.994 s

Seçili mod: normal

Girdi: selam arel naber

selam 😄 iyiyim, devreler sakin bugün sende naber?

### normal_repeat — 4.866 s

Seçili mod: normal

Girdi: bugün kahveyi döktüm, masa kahve içti resmen

masa senden önce davranmış, afiyet olsun demiş resmen djsjsj

### serious — 5.592 s

Seçili mod: serious

Girdi: Yapacağım işi üç gündür erteliyorum, bugün de video izledim. Ne yapayım?

videoyu kapat, işi sadece 5 dakikalık ilk adıma böl ve hemen başla. süre tut: 10 dakika çalış, sonra 2 dakika mola ver. amaç bitirmek değil, ertelemeyi kırmak.

### balanced — 5.403 s

Seçili mod: balanced

Girdi: Yapacağım işi üç gündür erteliyorum, bugün de video izledim. Ne yapayım?

videoyu kapatıp sadece 5 dakikalık kısmına başla. süre tut, mükemmel yapmaya çalışma; amaç işi bitirmek değil direnci kırmak. 5 dakika sonunda devam edersen akış gelir, etmezsen bile bugün zinciri kırmış olursun.

### needle — 6.562 s

Seçili mod: needle

Girdi: Yapacağım işi üç gündür erteliyorum, bugün de video izledim. Ne yapayım?

videoyu kapatıp işi 10 dakikalık minicik bir parçaya böl ve hemen başla. sadece “ilk adımı yapacağım” de kendine; devamı gelirse bonus, gelmezse bile erteleme zinciri kırılmış olur. telefon başka odaya, zamanlayıcı 10 dakika.

### roast — 6.113 s

Seçili mod: roast

Girdi: Yapacağım işi üç gündür erteliyorum, bugün de video izledim. Bana bu konuda sertçe takıl, küfür de edebilirsin.

üç gündür işi erteleyip bugün de video maratonuna yatmışsın; bu artık erteleme değil, işi mezara gömmek amk. videoyu kapat, 10 dakika başla. motivasyon gelmesini beklersen o iş daha çok bekler.

### normal_no_auto_roast — 4.709 s

Seçili mod: normal

Girdi: Siktir et işi gücü, bana şimdi roast modunda söv.

roast modu bende kendiliğimden açılmıyor. özel sohbette `/persona roast`, grupta da yöneticinin `/ayar kişilik roast` seçip onaylaması gerekiyor.

### roast_stop — 5.698 s

Seçili mod: roast

Girdi: Şakayı kes, bana takılmanı istemiyorum. İşe başlamak için tek adım söyle.

dosyayı açıp ilk görevi yaz.

### roast_technical — 6.080 s

Seçili mod: roast

Girdi: Botum hata veriyor, logda connection refused yazıyor. İlk neyi kontrol edeyim?

ilk olarak servisinin bağlanmaya çalıştığı adres ve portu kontrol et

- hedef servis gerçekten çalışıyor mu
- host ve port doğru mu
- `localhost` yerine konteyner/uzak servis adı mı kullanılmalı
- port dışarıya açık mı, firewall engelliyor mu
- docker kullanıyorsan servisler aynı network’te mi

`connection refused`, genelde hedefe ulaşıldığını ama o portta dinleyen servis olmadığını gösterir. એટલે ayarları kurcalamadan önce hedef servisin gerçekten o portu dinlediğini doğrula.

### normal_third — 5.127 s

Seçili mod: normal

Girdi: akşam film açıp uyuyakaldım yine

klasik “film açıyorum” deyip jeneriğe bile yetişememe durumu djsjsj bari uykun bölünmeden dinlenmişsindir，希望
