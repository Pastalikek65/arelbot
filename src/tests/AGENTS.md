# Test kurallarÄ±

- DÄ±ÅŸ HTTP Ã§aÄŸrÄ±larÄ± fixture veya replay ile taklit edilir.
- GerÃ§ek MongoDB/Redis testleri ayrÄ± integration profiline konur.
- PlatformlarÄ±n canlÄ± HTML'ine baÄŸlÄ± test kabul kriteri olamaz.
- Her provider iÃ§in baÅŸarÄ±, timeout, 429/5xx, bozuk schema ve stale cache senaryosu bulunur.
- Test verisi secret, kiÅŸisel mesaj veya canlÄ± cookie iÃ§ermez.
- Async testlerde aÃ§Ä±k timeout kullanÄ±lÄ±r; flaky sleep tercih edilmez.
