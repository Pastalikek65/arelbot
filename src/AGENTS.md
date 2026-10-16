# `src` modÃ¼l kurallarÄ±

- `features` Telegram'dan baÄŸÄ±msÄ±z domain sÃ¶zleÅŸmelerini ve provider'larÄ± iÃ§erir.
- `handlers` yalnÄ±zca update parsing, permission ve response formatting yapar.
- `storage` ve `database` kalÄ±cÄ± veri eriÅŸimidir; servisler collection nesnesi Ã¼retmez.
- `jobs` aÄŸÄ±r/tekrar denenebilir iÅŸlerin yaÅŸam dÃ¶ngÃ¼sÃ¼nÃ¼ yÃ¶netir.
- `security` ortak SSRF, redaction, sanitizer ve rate limit politikalarÄ±nÄ±n tek kaynaÄŸÄ±dÄ±r.
- Provider'lar ortak `ProviderRequest`, `ProviderResponse` ve `ProviderHealth` tiplerini kullanÄ±r.
- Bir feature'Ä±n Ã§Ä±ktÄ±sÄ± `FeatureResult` zarfÄ±na Ã§evrilmeden Telegram'a gÃ¶nderilmez.
- Async kodda bloklayan dosya/aÄŸ iÅŸlemi event loop iÃ§inde doÄŸrudan Ã§alÄ±ÅŸtÄ±rÄ±lmaz.
