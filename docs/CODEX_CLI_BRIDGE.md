# Arel için normal Codex CLI bağlantısı

Arel, OpenAI sohbet rotasında normal Codex CLI'nin headless `codex exec`
komutunu kullanabilir. Uygulama bir OAuth dosyası okumaz ve bir API anahtarını
CLI'ye geçirmez; hesabın oturum durumu `CODEX_HOME` altında CLI tarafından
yönetilir.

Üretim ayarları:

```text
OPENAI_CHATGPT_ENABLED=true
OPENAI_CHATGPT_MODE=cli
OPENAI_CHATGPT_CLI_BIN=codex
OPENAI_CHATGPT_CLI_MODEL=
OPENAI_CHATGPT_CODEX_HOME=/run/arel-openai/codex
OPENAI_CHATGPT_CODEX_HOST_DIR=/root/.codex
```

`OPENAI_CHATGPT_CLI_MODEL` boş bırakılırsa Codex hesabının varsayılan modeli
kullanılır. Belirli bir model seçilecekse yalnızca CLI'nin desteklediği model
adı yazılır. Arel kısa ve hızlı sohbetlerde düşünme/akıl yürütme ayarı
göndermez; `--sandbox read-only`, `--ephemeral` ve `--skip-git-repo-check`
ile yalnızca yanıt üretir.

## Failover davranışı

CLI binary'si yoksa, hesap oturumu yoksa, CLI 401/429 döndürürse, zaman aşımına
uğrarsa veya bozuk JSONL üretirse `ChatGPTCliProvider` yalnızca güvenli bir
hata kodu üretir. `AIProviderRouter` OpenAI'ı devre dışı bırakıp EVREN ve
yapılandırılmış diğer sağlayıcılara geçer. Bu hata kullanıcıya “AI servisleri
yoğun” gibi yanıltıcı bir mesaj olarak yansıtılmaz.

Görsel, embedding, transcribe ve Arel araç çağrıları CLI rotasına verilmez;
capability filtresi bunları ilgili typed sağlayıcılara bırakır. Böylece Codex
CLI'nin desteklemediği bir kabiliyeti varmış gibi göstermeyiz.

## Sunucu kurulumu

Sunucuda bir kez normal Codex hesabıyla giriş yapılır:

```bash
codex login status
codex login
```

Docker Compose bot ve worker imajında aynı resmi `@openai/codex` CLI sürümünü
kurar. Host'taki `OPENAI_CHATGPT_CODEX_HOST_DIR` dizini yalnızca Codex home
olarak `/run/arel-openai/codex` yolundan bağlanır. CLI kendi oturum ve geçici
metadata dosyalarını yazabilsin diye bu dizin yazılabilirdir; Arel token,
prompt, CLI çıktısı veya tam hesap yolunu loglamaz. Oturum yoksa servis yine
açılır ve otomatik olarak diğer sağlayıcılarla devam eder.

Salt-okunur bounded canlı kontrol:

```bash
codex exec --skip-git-repo-check --ephemeral --json - <<'EOF'
Sadece kısa bir sağlık yanıtı ver: codex-ok
EOF
```

Bu komut yalnızca staging/operatör kontrolünde çalıştırılır; CI canlı hesap
çağrısı yapmaz. Sağlık ekranı yalnızca `cli_ready`, `cli_not_found` veya
`cli_auth_required` gibi sınırlı durum kodlarını gösterir.
