# `src` modül kuralları

- `features` Telegram'dan bağımsız domain sözleşmelerini ve provider'ları içerir.
- `handlers` yalnızca update parsing, permission ve response formatting yapar.
- `storage` ve `database` kalıcı veri erişimidir; servisler collection nesnesi üretmez.
- `jobs` ağır/tekrar denenebilir işlerin yaşam döngüsünü yönetir.
- `security` ortak SSRF, redaction, sanitizer ve rate limit politikalarının tek kaynağıdır.
- Provider'lar ortak `ProviderRequest`, `ProviderResponse` ve `ProviderHealth` tiplerini kullanır.
- Bir feature'ın çıktısı `FeatureResult` zarfına çevrilmeden Telegram'a gönderilmez.
- Async kodda bloklayan dosya/ağ işlemi event loop içinde doğrudan çalıştırılmaz.
