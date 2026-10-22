# d2api

DeepSeek Web hesabını (`chat.deepseek.com`) OpenAI ve Anthropic uyumlu bir API'ye çeviren küçük, sıfır bağımlılıklı bir proxy.

- `POST /v1/chat/completions` — OpenAI Chat Completions (stream + non-stream)
- `POST /v1/messages` — Anthropic Messages (Claude Code / Anthropic SDK)
- `GET /v1/models`, `GET /v1/model-capabilities`
- Thinking (`reasoning_content`), web search + kaynaklar, tool calling
- Çoklu hesap, round-robin, otomatik token yenileme (email/parola varsa)
- `telegram-bot.js` — Telegram botu: stream edit, sohbet hafızası, `/think` / `/search` komutları
- PoW (`DeepSeekHashV1`) çözümü paket içindeki wasm ile yapılır

Node 18+ dışında hiçbir şey gerekmez. `npm install` yok.

---

## 1. Hızlı başlangıç

```powershell
cd C:\Opencode\d2api
node login.js add --token "BURAYA_TOKEN"
node server.js
```

Sunucu: `http://127.0.0.1:9655`

```powershell
curl.exe http://127.0.0.1:9655/v1/chat/completions `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer herhangi-bir-sey" `
  -d '{"model":"deepseek-v4.1-flash","messages":[{"role":"user","content":"merhaba"}]}'
```

`PROXY_API_KEY` boşsa `Authorization` başlığı kontrol edilmez; istemci ne yazarsa yazsın kabul edilir. Dışarı açacaksan `PROXY_API_KEY` koy.

---

## 2. Token alma

Token, `chat.deepseek.com` üzerindeki `completion` isteğinin **Request Headers** kısmında durur:

1. https://chat.deepseek.com adresine giriş yap.
2. `F12` → **Network** sekmesi.
3. Bir sohbet mesajı gönder.
4. Listede **`completion`** isteğine tıkla (200, xhr).
5. Sağda **Headers** → **Request Headers** → şu satırı bul:
   ```
   authorization: Bearer eyJ... veya K+4...
   ```
6. `Bearer ` kısmını at, kalan değeri şu komuta ver:
   ```powershell
   node login.js add --token "BURAYA"
   ```

> Bazı sürümlerde **localStorage boş olabilir**. `userToken` görünmüyorsa sorun değil; header'daki `authorization` değeri asıl kaynaktır. Aynı istekteki `x-ds-pow-response` **token değildir** — PoW cevabıdır, tek kullanımlıktır; onu kopyalama.

### Sadece cookie varsa

`authorization` header'ı görünmüyorsa aynı `completion` isteğinin **`cookie:`** satırını kopyala:

```powershell
node login.js add --cookie 'ds_session_id=...; d_id=...; ...'
```

Proxy bu cookie ile `/api/v0/users/current` çağırır, bearer token'ı çıkarır ve `data/accounts.json` içine kaydeder. Tek başına `ds_session_id` genelde yeterlidir; tam Cookie header'ı vermek daha güvenlidir.

### Email/parola ile oturum

```powershell
node login.js add --email "mail@example.com" --password "parola"
```

DeepSeek login endpoint'i captcha/browser doğrulaması isterse **HTTP 202 + boş gövde** döndürür. Bu durumda token kopyalama yolu tek seçenektir.

Token eklerken proxy gerçek bir oturum açıp PoW çözer. Başarılıysa `session OK`, `PoW OK` ve `Saved acc_1` yazar. `node login.js test` ile sonradan doğrulayabilirsin.

### Birden fazla hesap

```powershell
node login.js add --token "TOKEN_1" --label "hesap-1"
node login.js add --token "TOKEN_2" --label "hesap-2"
node login.js list
node login.js test
```

Hesaplar `data/accounts.json` içinde tutulur. Round-robin ile dönülür; 429/401 alan hesap kısa süre cooldown'a girer.

> `data/accounts.json` ve `.env` içinde token/parola vardır. Bu dosyaları paylaşma, git'e ekleme.

---

## 3. Modeller

Web tarafı Eylül 2026'da tek moda geçti: **DeepSeek-V4.1-Flash**. Model adı sadece iki anahtarı belirler:

| Model sonu | thinking_enabled | search_enabled |
|---|---|---|
| `deepseek-v4.1-flash` | hayır | hayır |
| `deepseek-v4.1-flash-think` | evet | hayır |
| `deepseek-v4.1-flash-search` | hayır | evet |
| `deepseek-v4.1-flash-think-search` | evet | evet |

Kabul edilen isimler:

```
deepseek-v4.1-flash
deepseek-v4.1-flash-think
deepseek-v4.1-flash-search
deepseek-v4.1-flash-think-search
deepseek-v4-flash / -think / -search / -think-search
deepseek-chat / -think / -search / -think-search
deepseek-reasoner / deepseek-reasoner-search
deepseek-r1 / deepseek-r1-search
deepseek-expert / deepseek-expert-search
deepseek-v4-pro / -think / -search / -think-search
```

Ek alias'lar: `deepseek-v3`, `deepseek-v3.1`, `deepseek-v3.2`, `deepseek-instant`, `deepseek-default` ve bunların `-think` / `-search` / `-think-search` varyantları. Hepsi aynı V4.1-Flash modeline gider; fark sadece iki anahtardır.

- `reasoner`, `r1`, `-think` → thinking açık.
- `-search` → web arama açık.
- Bilinmeyen model adı gelirse `DEFAULT_MODEL` (varsayılan `deepseek-v4.1-flash`) kullanılır; böylece `gpt-4o` gibi isimler gönderen istemciler de çalışır.
- `reasoning_effort` (none değilse) thinking'i açar.
- `web_search_options` / `search: true` aramayı açar.

---

## 4. Thinking

Thinking açıkken:

- Non-stream: `choices[0].message.reasoning_content`
- Stream: `choices[0].delta.reasoning_content`

Cevap metni her zamanki `content` alanındadır. Reasoner modellerinde thinking genelde cevaptan önce akar.

---

## 5. Web arama + kaynaklar

`-search` modellerinde veya `web_search_options` ile arama açılır.

DeepSeek cevap içine `[citation:0]` benzeri işaretler koyar. Proxy bunları temizler ve kaynakları OpenAI uyumlu `annotations` alanına koyar:

```json
"annotations": [
  {
    "type": "url_citation",
    "url_citation": {
      "url": "https://example.com/page",
      "title": "Example page",
      "start_index": 0,
      "end_index": 0
    }
  }
]
```

Stream'de son delta içinde `annotations` gelir. Bazı istemciler bu alanı göstermez; kaynakları görmek için non-stream kullan.

---

## 6. Tool calling

DeepSeek Web'in native function calling'i yok. Proxy, OpenAI/Anthropic `tools` tanımlarını system prompt'a enjekte eder ve modelin ürettiği JSON'u yakalayıp standart `tool_calls` / `tool_use` cevabına çevirir.

Desteklenen girişler:

- OpenAI: `{"type":"function","function":{"name":...,"description":...,"parameters":{...}}}`
- Anthropic: `{"name":...,"description":...,"input_schema":{...}}`

Model şu formatta cevap verir (proxy bunu bekler ve istemciye çevirir):

```json
{"tool_call":{"name":"get_weather","arguments":{"city":"Istanbul"}}}
```

Kurallar:

- Bir turda bir tool önerilir; paralel `tool_calls` dizisi de parse edilir.
- İstemci, assistant mesajını `tool_calls` ile ve sonucu `role: "tool"` + `tool_call_id` ile geri göndermelidir (standart OpenAI akışı).
- Tool'lu isteklerde cevap, geçerli JSON tamamlanmadan istemciye gönderilmez. Bu yüzden tool turunda `content` stream'i gecikebilir; thinking akışı yine canlıdır.
- Modelin uydurduğu tool isimleri kabul edilmez; sadece tanımlı isimler `tool_calls` olarak döner.

---

## 7. İstemci bağlama

### OpenAI SDK / Open WebUI / Cline / Roo / SillyTavern

- Base URL: `http://127.0.0.1:9655/v1`
- API Key: `PROXY_API_KEY` boşsa herhangi bir değer
- Model: `deepseek-v4.1-flash` (thinking için `...-think`, arama için `...-search`)

### Claude Code

PowerShell:

```powershell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:9655"
$env:ANTHROPIC_AUTH_TOKEN = "herhangi-bir-şey"
$env:ANTHROPIC_MODEL = "deepseek-v4.1-flash-think"
claude
```

`ANTHROPIC_MODEL` bilinen bir model adıysa thinking/search bayrakları ona göre ayarlanır. Bilinmeyen `claude-*` adları `ANTHROPIC_MODEL` / `DEFAULT_MODEL` değerine düşer.

### cURL (stream)

```powershell
curl.exe http://127.0.0.1:9655/v1/chat/completions `
  -H "Content-Type: application/json" `
  -d '{"model":"deepseek-v4.1-flash-think","stream":true,"messages":[{"role":"user","content":"düşün ve cevapla"}]}'
```

---

## 8. Telegram botu

Proxy'yi Telegram'da kullanmak için `telegram-bot.js` var. Ek bağımlılık yok; proxy ile aynı makinede çalışabilir (farklı makinedeyse `D2API_BASE_URL` ayarla). Long polling kullanır: webhook, public IP veya HTTPS gerekmez.

### Bot oluşturma

1. Telegram'da **@BotFather** → `/newbot`.
2. Bir ad ve `_bot` ile biten kullanıcı adı ver.
3. Verdiği token'ı `.env` içine koy (token'ı kimseyle paylaşma):
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   ```
4. Botu başlat, `/id` yaz. Çıkan `user_id` değerini `.env`'e ekle (bot çalışıyorsa yeniden başlat):
   ```
   TELEGRAM_ALLOWED_USER_IDS=123456789
   ```
   Boş bırakırsan botu bulan herkes senin DeepSeek hesabını kullanabilir; doldur.
5. Proxy çalışıyor olsun, sonra:
   ```powershell
   node telegram-bot.js
   ```
   veya `npm run bot`.

Bot proxy'ye `D2API_BASE_URL` (varsayılan `http://127.0.0.1:9655/v1`) üzerinden bağlanır. `PROXY_API_KEY` doluysa aynı anahtarı otomatik gönderir.

### Komutlar

| Komut | Açıklama |
|---|---|
| `/think [on\|off]` | Derin düşünme (reasoning) aç/kapat |
| `/search [on\|off]` | Web araması aç/kapat |
| `/model` | Mevcut modeli göster |
| `/model flash\|think\|search\|both` | Hazır model modu |
| `/model <deepseek-model-id>` | Belirli bir model ID'si |
| `/reset` | Sohbet hafızasını sıfırla |
| `/system [metin\|reset]` | Bu sohbet için sistem promptu |
| `/status` | Model, hafıza, uptime |
| `/id` | Kullanıcı ve sohbet ID'leri |
| `/help` | Komut listesi |

### Davranış

- Her sohbet için ayrı geçmiş: son 24 mesaj / 80k karakter (`.env` ile ayarlanır).
- Aynı sohbette istekler sırayla işlenir; eşzamanlı mesajlar birbirine karışmaz.
- Cevap canlı düzenlenir (stream edit). 4096 karakteri aşan cevaplar birden fazla mesaja bölünür; kod blokları mümkün olduğunca dengelenir.
- Markdown → Telegram HTML çevrilir. Telegram parse hatası verirse aynı mesaj düz metin olarak tekrar gönderilir.
- Web aramasında proxy'nin döndürdüğü kaynaklar cevabın sonuna **Kaynaklar** olarak eklenir.
- Ayarlar `data/telegram-state.json` içinde kalıcıdır. Geçmiş RAM'de tutulur; restart'ta sıfırlanır (`/reset` de temizler).
- Gruplarda botu `@kullaniciadi` ile mention et veya mesajına **reply** ver. BotFather'da privacy mode kapalıysa gruptaki tüm mesajları görür; açıksa sadece komutlar, mention'lar ve reply'lar gelir. Privacy mode'u kapatmak için @BotFather → `/setprivacy` → botunu seç → **Disable**. Grup geçmişi sohbetin tamamı için ortaktır.
- Sadece metin işlenir; fotoğraf, dosya ve sticker'lar yok sayılır.
- Tool calling Telegram botunda kullanılmaz; düz sohbet eder. Tool'lu kullanım için API'yi başka istemciden kullan.
- `/think` veya `/search` ile oynarken özel `/model <id>` seçimi `deepseek-v4.1-flash` ailesine döner.

### Linux sunucu (systemd)

Node 18+ kurulu olsun. Proje `/opt/d2api` altında duracak şekilde:

```bash
sudo mkdir -p /opt/d2api
sudo rsync -a ./ /opt/d2api/ --exclude data --exclude .env
cd /opt/d2api

sudo cp .env.example .env
sudo nano .env          # TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS, PROXY_API_KEY

sudo useradd --system --home /opt/d2api --shell /usr/sbin/nologin d2api
sudo mkdir -p data
sudo chown -R d2api:d2api /opt/d2api
sudo chmod 700 data

# DeepSeek token'ını d2api kullanıcısı olarak ekle
read -s -p "DeepSeek token: " DS_TOKEN; echo
sudo -u d2api node login.js add --token "$DS_TOKEN"
unset DS_TOKEN

sudo cp deploy/d2api.service deploy/telegram-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now d2api telegram-bot
sudo systemctl status d2api telegram-bot
```

Loglar:

```bash
sudo journalctl -u d2api -f
sudo journalctl -u telegram-bot -f
```

Notlar:

- `deploy/*.service` içinde `User=d2api` ve `WorkingDirectory=/opt/d2api` varsayılır. Farklı kullanıcı/yol kullanacaksan unit dosyalarını düzenle. Node yolu farklıysa (ör. `/usr/local/bin/node`) `ExecStart` satırını güncelle.
- Proxy yalnızca `127.0.0.1:9655` dinler; dışarı açma. Bot dışarıya sadece Telegram API'sine bağlanır.
- `.env` ve `data/` içinde token/parola vardır: `chmod 600 .env`, `chmod 700 data`.
- Güncelleme: dosyaları kopyala, `sudo systemctl restart d2api telegram-bot`.
- Windows'ta iki ayrı terminalde `node server.js` ve `node telegram-bot.js` çalıştır; kalıcı hale getirmek için NSSM veya Görev Zamanlayıcı kullanılabilir.

---

## 9. Yapılandırma

`.env.example` dosyasını `.env` olarak kopyala; gerçek ortam değişkenleri `.env`'i ezer.

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `PORT` | `9655` | Dinlenecek port |
| `HOST` | `127.0.0.1` | Dinlenecek adres |
| `PROXY_API_KEY` | boş | Doluysa istemci `Bearer` / `x-api-key` göndermeli |
| `DEEPSEEK_TOKEN` / `DEEPSEEK_TOKENS` | boş | Dosyaya eklemeden hızlı token(lar), virgülle ayrılır |
| `DEEPSEEK_COOKIE` | boş | Opsiyonel tam Cookie başlığı |
| `D2API_DATA_DIR` | `./data` | `accounts.json` ve `telegram-state.json` klasörü |
| `DEFAULT_MODEL` | `deepseek-v4.1-flash` | Bilinmeyen model adlarında kullanılır |
| `DEEPSEEK_MODEL_TYPE` | `default` | Boş bırakılırsa alan gönderilmez |
| `MAX_PROMPT_CHARS` | `220000` | Prompt karakter sınırı |
| `MAX_CONCURRENT` | `8` | Aynı anda işlenecek istek |
| `MAX_ACCOUNT_ATTEMPTS` | `3` | Hesap deneme sayısı |
| `DEEPSEEK_TIMEOUT_MS` | `60000` | Kısa istekler için timeout |
| `DEEPSEEK_STREAM_TIMEOUT_MS` | `60000` | Stream bağlantı timeout'u |
| `DEEPSEEK_STREAM_IDLE_TIMEOUT_MS` | `180000` | Stream veri gelmezse kesme süresi |
| `STREAM_KEEPALIVE_MS` | `10000` | SSE ping aralığı |
| `DELETE_SESSIONS` | `1` | İş bitince web oturumunu sil |
| `LOG_REQUESTS` | `1` | İstek logları |
| `CORS_ORIGIN` | `*` | CORS origin |
| `ANTHROPIC_MODEL` | `deepseek-v4.1-flash` | Bilinmeyen Claude adları için |
| `DEEPSEEK_BASE_URL` | `https://chat.deepseek.com` | Test/mirror için |
| `DEEPSEEK_LOCALE` | `en_US` | `x-client-locale` |
| `DEEPSEEK_DEVICE_ID` | otomatik | Login cihaz kimliği |
| `TELEGRAM_BOT_TOKEN` | boş | BotFather token'ı; Telegram botu için gerekli |
| `TELEGRAM_ALLOWED_USER_IDS` | boş | Virgülle ayrılmış izinli kullanıcı ID'leri |
| `TELEGRAM_ALLOWED_CHAT_IDS` | boş | Opsiyonel izinli sohbet ID'leri |
| `D2API_BASE_URL` | `http://127.0.0.1:9655/v1` | Botun bağlandığı proxy adresi |
| `TELEGRAM_SYSTEM_PROMPT` | boş | Varsayılan sistem promptu |
| `TELEGRAM_HISTORY_MESSAGES` | `24` | Sohbet başına saklanan mesaj |
| `TELEGRAM_HISTORY_CHARS` | `80000` | Sohbet geçmişi karakter sınırı |
| `TELEGRAM_MAX_MESSAGE_CHARS` | `3800` | Tek Telegram mesajı sınırı |
| `TELEGRAM_EDIT_INTERVAL_MS` | `1200` | Stream edit aralığı |
| `TELEGRAM_STREAM_TIMEOUT_MS` | `600000` | Botun proxy istek timeout'u |

---

## 10. Sorun giderme

**401 / invalid token**
Token süresi dolmuş olabilir. Tarayıcıdan yenisini al ve `node login.js add --token ...` ile ekle. `node login.js test` ile doğrula.

**`chat_session create` hatası**
Token geçersiz, captcha/cihaz doğrulaması takılmış veya web protokolü değişmiş olabilir. Tarayıcıda oturumu yenile, yeni token al. Gerekirse `accounts.json` içindeki hesaba `"cookie": "..."` ve `"hif_dliq"`, `"hif_leim"` alanlarını ekle (Network sekmesindeki aynı isteklerden alınır).

**429 / "All DeepSeek accounts are cooling down"**
Web hesabı rate limit yemiş. Birkaç dakika bekle veya ikinci hesap ekle. Aynı anda çok istek atma; `MAX_CONCURRENT` değerini düşür.

**Boş cevap**
Hesap limitli veya oturum reddedilmiş olabilir. `node login.js test --full` ile dene. Loglarda upstream hatası var mı bak.

**PoW hatası**
`assets/deepseek_hash.wasm` dosyası duruyor mu kontrol et. CDN'den otomatik indirme için `DEEPSEEK_WASM_URL` ayarla.

**Stream çok yavaş başlıyor**
Thinking + tool birlikte kullanılıyorsa normal. Thinking kapalı modelle dene. Ağdan kaynaklıysa `DEEPSEEK_STREAM_TIMEOUT_MS` değerini artır.

**Telegram botu cevap vermiyor**
`TELEGRAM_BOT_TOKEN` doğru mu, proxy çalışıyor mu (`curl http://127.0.0.1:9655/health`) kontrol et. `TELEGRAM_ALLOWED_USER_IDS` doluysa `/id` ile kendi ID'nin listede olduğundan emin ol. `journalctl -u telegram-bot -f` loglarına bak.

**Telegram 401 / bot başlamıyor**
BotFather token'ı yanlış veya iptal edilmiş. BotFather'dan yeni token al, `.env` içindeki `TELEGRAM_BOT_TOKEN` değerini güncelle ve botu yeniden başlat.

**Telegram 409 Conflict**
Aynı bot token'ıyla iki bot süreci çalışıyor. Eskisini durdur (`systemctl stop telegram-bot` veya eski terminal). Long polling ile webhook aynı anda kullanılamaz; bot başlarken webhook'u siler.

**Telegram "can't parse entities"**
Bot otomatik olarak düz metne düşer. Sık oluyorsa modelin ürettiği Markdown çok bozuktur; `/model flash` ile sade modeli dene.

---

## 11. Sınırlamalar

- Web API resmi değildir; DeepSeek haber vermeden değiştirebilir.
- Sadece metin: görsel/dosya yükleme yok.
- Proxy her isteği tam geçmişle, yeni bir web oturumunda çalıştırır (stateless). İstemci tüm konuşma geçmişini göndermelidir.
- `temperature`, `top_p`, `max_tokens` gibi üretim parametreleri web tarafında uygulanmaz; sessizce yok sayılır.
- Tool calling prompt enjeksiyonu ile yapılır; model bazen formatı bozabilir. Proxy yaygın formatları tolere eder ama %100 garanti yoktur.
- Web hesabı kullanımı DeepSeek'in kullanım şartlarına tabidir. Aşırı/otomatik kullanım hesabın geçici veya kalıcı kısıtlanmasına yol açabilir. Kendi hesabınla, makul yük altında kullan.

---

## 12. Geliştirme

```powershell
node test/run.js          # mock upstream ile uçtan uca API testi (hesap gerekmez)
node test/telegram-run.js # sahte Telegram + proxy ile bot testi
node test/live.js         # kayıtlı gerçek hesapla canlı test (birkaç mesaj harcar)
```

`test/run.js`; modeller, non-stream, stream+thinking, tool call, stream tools, tool-result turu, search+citation ve Anthropic akışlarını kapsar. Gerçek hesap gerekmez.

`test/telegram-run.js`; Markdown/HTML dönüşümü, mesaj parçalama, komut ayrıştırma, stream edit, typing action, sohbet geçmişi, web arama kaynakları ve `/help`, `/id`, `/reset` komutlarını sahte Telegram API'siyle test eder. Gerçek bot token'ı gerekmez.

`test/live.js`; gerçek hesapta web aramayı, tool sonuç turunu ve thinking stream'ini dener. `data/accounts.json` içinde doğrulanmış bir hesap olmalıdır.

### Referanslar

Protokol ve PoW davranışı için şu açık kaynak projelerden faydalanıldı:

- `ForgetMeAI/FreeDeepseekAPI` — güncel V4.1 birleşik mod, tool protokolü
- `a0yark/deepseek2api` — fragment/OT SSE protokolü, PoW wasm
- `CJackHwang/ds2api` — DeepSeekHashV1 referansı ve SSE kenar durumları

Bu proje bağımsızdır, DeepSeek ile bağlantılı değildir.
