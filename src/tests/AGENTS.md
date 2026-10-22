# Test kuralları

- Dış HTTP çağrıları fixture veya replay ile taklit edilir.
- Gerçek MongoDB/Redis testleri ayrı integration profiline konur.
- Platformların canlı HTML'ine bağlı test kabul kriteri olamaz.
- Her provider için başarı, timeout, 429/5xx, bozuk schema ve stale cache senaryosu bulunur.
- Test verisi secret, kişisel mesaj veya canlı cookie içermez.
- Async testlerde açık timeout kullanılır; flaky sleep tercih edilmez.
