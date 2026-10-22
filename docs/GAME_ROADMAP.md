# Arelbot Oyun Platformu — Üretim Yol Haritası ve Oyun Sözleşmesi

**Belge sürümü:** 1.0  
**Durum:** Tasarım ve uygulama sözleşmesi  
**Son güncelleme:** 2026-10-17  
**Kapsam:** Telegram özel sohbetleri, gruplar ve forum konuları

Bu belge oyunların yalnızca isimlerini sıralamaz. Her oyun ailesinin oyuncuya
yaşatacağı deneyimi, Telegram kontrol yüzeyini, restart sonrasında korunacak
durumu, puan ve ekonomi kurallarını, hile önleme sınırlarını, kabul kapılarını
ve uygulama bağımlılıklarını tanımlar. Sayısal değerler ilk sürümde
**[PLACEHOLDER]** kabul edilir; replay ve yük testleriyle kalibre edilmeden
üretim dengesi olarak yorumlanamaz.

## 1. Kanıta dayalı mevcut durum

Kod incelemesinde şu yüzeyler ve altyapılar görüldü:

- **src/games/catalog.py:** GameDefinition ve GameSession; oyuncu sınırı,
  tur süresi, şema sürümü, state anahtarı ve tahta bağlantı uzunluğu.
- **src/games/state_store.py:** sürümlü oturum, compare-and-save,
  snapshot/restore ve sohbet kapsamlı lookup. Üretimde kalıcı repository,
  testte bellek store kullanılıyor.
- **src/games/engine.py:** tur doğrulama, timeout, zorluk profili, combo ve
  puan hesapları.
- **src/games/action_ledger.py:** Telegram callback tekrarında aynı action'ın
  ikinci kez işlenmesini engelleyen idempotent claim.
- **LeaderboardService:** event kimliğiyle puan kaydı; **EconomyService:**
  sohbet + kullanıcı kapsamında bakiye ve işlem event'leri.
- Mevcut oyunlar: kelime türetme, anlatmaca, adam asmaca, quiz, XOX,
  Connect Four, Batak, Blackjack, şişe/doğruluk-cesaret ve rulet/düello
  servisleri.
- XOX yeni turlarda 6×6, 7×7 veya 8×8 tahta ve dört aynı taş kuralına sahip;
  eski 3×3 snapshot'ları geriye uyumlu okunuyor.
- BlackjackService, BatakService ve RouletteService kalıcı state ve action
  ledger kullanıyor. Eski kelime akışlarının bazıları hâlâ context.chat_data
  uyumluluk yoluna sahip; bunlar ortak GameSession'a taşınmalı.
- Telegram menü ve callback'lerde lobby, zorluk, ipucu, durdurma ve hamle
  butonları mevcut. Büyük tahta ve uzun liste için ortak pagination gerekiyor.
- Quiz, kelime ve bazı kart metinlerinde tarihî mojibake görülüyor. Yeni
  içerik UTF-8 doğrulamasından geçmeden üretime alınamaz.

### Korunacak invariants

1. State, handler içinde kalıcılaştırılmaz; domain service ve repository
   sınırı kullanılır.
2. Callback action'ı ledger ile claim edilmeden state veya ödül değişmez.
3. Her aktif oturum sohbet + oyun + varsa forum konusu kapsamında tektir.
4. Durum, sürüm, son action, oyuncular, timeout ve sonuç event'i doğrulanır.
5. Kullanıcı metni ve dışarıdan gelen kelimeler HTML-safe biçimde escape edilir.
6. Ödül yalnızca tamamlanmış domain event'inden çıkar; retry ikinci ödül vermez.
7. Oyun dışı mesajlar oyun state'ine yazılmaz; aktif oyun da medya, moderasyon
   ve AI yollarını yanlışlıkla hamle olarak tüketmez.

## 2. Ortak oyun sözleşmesi

### 2.1 GameDefinition metadata

Her oyun registry kaydında şu alanlar bulunur: key, label, category,
min_players, max_players, turn_timeout_seconds, state_schema_version,
supports_difficulty, supports_hints, score_model, reward_policy,
max_action_bytes, feature_flag, timeout_ms, cache_ttl ve audit_policy.

Kategori değerleri word, board, quiz, card, social, puzzle veya strategy
olabilir. Public komut, callback, AI tool ve inline yüzeyi aynı key'i kullanır.

### 2.2 Session ve event

GameSession alanları: session_id, game_key, chat_id, thread_id, owner_id,
players (user_id, display_name snapshot, seat/team), phase, turn_user_id,
turn_index, expires_at, state_schema_version, JSON uyumlu state,
processed_actions, last_event_id, version, score_snapshot, economy_snapshot,
started_at, updated_at ve finished_at.

GameEvent alanları: event_id, session_id, game_key, chat_id, thread_id,
actor_id, action_type, action_payload_hash, before_version, after_version,
outcome, score_delta, coin_delta ve created_at.

- Payload yalnız allowlist edilmiş küçük alanlardan oluşur; secret, dosya yolu
  veya tam kullanıcı metni yazılmaz.
- Event kimliği ve action key unique index ile korunur. CAS yarışında kaybeden
  callback yeni görünümü alır, ikinci ödül üretmez.
- Timeout worker lease claim ve sürüm kontrolü yapar; eski timer yeni round'ı
  kapatamaz.
- Restart sonrası aktif oturum expires_at ile değerlendirilir; bozuk state
  dead-letter'a yazılır.

### 2.3 Telegram buton sözleşmesi

Callback biçimi: oyun, kısa oturum kimliği, action ve sınırlandırılmış değerden
oluşan ASCII token'dır. Katıl/ayrıl, başlat, hamle/cevap, ipucu, durdur,
yeniden oyna, liderlik ve sayfa ileri/geri ortak butonlardır. Süresi geçmiş
callback state değiştirmez; query güvenli cevapla sonlandırılır.

### 2.4 Puan ve ekonomi

Puan rekabet, coin sanal ilerleme, XP uzun dönem profil içindir. Günlük ve
oyun başı ödül tavanları **[PLACEHOLDER]** olarak feature manifestinde
tanımlanır. Admin/debug action'ı ranked event üretmez. Negatif bakiye,
duplicate event ve yarış koşulu EconomyService tarafından reddedilir.

### 2.5 Anti-cheat temel paketi

- Kimlik Telegram update'ten alınır; callback payload yetki vermez.
- Sıra, rol, chat/thread, state version ve süresi domain'de doğrulanır.
- Kelime/cevap canonical Türkçe normalizer ve uzunluk sınırından geçer.
- Random seed server-side saklanır; gerekirse commitment replay için tutulur.
- Hızlı tekrar, günlük ödül ve ipucu rate limit ile sınırlanır.
- Şüpheli seri metric/audit olarak işaretlenir; otomatik haksız ban yapılmaz.
- Reset, restore, sezon düzeltmesi audit'e yazılır.

## 3. Oyun aileleri

Her bölümde **Döngü**, **UX**, **State**, **Skor**, **Anti-cheat**, **Kabul**
ve **Bağımlılık** alanları bilinçli olarak aynı biçimde tutulur.

### 3.1 Kelime Türetme — word_game (mevcut)

**Döngü:** Harf rafı açılır; geçerli, daha önce bulunmamış kelime gönderilir.
Uzunluk, hız ve combo anlık geri bildirimdir; hedef [PLACEHOLDER] kelimeyi
tamamlamak, uzun vadeli hedef kategori ustalığıdır.

**UX:** Kategori → zorluk → harf rafı. Karıştır, ipucu, pas, kurallar,
durdur ve yeniden oyna butonları. Yalnız aktif chat/thread ve round'da
gelen kullanıcı kelimesi tüketilir; bot mesajı cevap sayılmaz. Kategori
listesi cursor ile sayfalanır.

**State:** rack, valid_words_hash, found_words, target_count, category,
difficulty, hints_used, shuffle_count, combo, last_actor_id,
last_actor_message_id, started_at, expires_at, schema_version.

**Skor:** Kelime uzunluğu + hız + combo; ipucu ve tekrar cezası. Tamamlanmış
round event'i başına [PLACEHOLDER] coin, günlük tavanla.

**Anti-cheat:** Sözlük server-side snapshot; canonical form bir kez sayılır;
callback valid kelime listesi gönderemez.

**Kabul:** Türkçe/ASCII alias aynı kelimeye gider; her ipucu ilerler; 100 aktif
chat'te state çaprazlanmaz; replay ikinci ödül vermez.

**Bağımlılık:** P0 state, P1 veri kalitesi, P2 pagination, P5 leaderboard.

### 3.2 Anlatmaca / Kelime Tahmini — word_guess (mevcut)

**Döngü:** Kategoriye uygun gizli kelime seçilir; ipuçları belirsizliği
azaltır; düşük deneme ve kısa süreyle cevaplanır.

**UX:** Kategori, zorluk, ipucu, atla, cevapla ve bitir butonları. hint_index
her action'da ilerler; eski callback aynı ipucunu yeniden vermez. Cevap
mesajı yalnız awaiting_guess state'inde ve doğru thread'de tüketilir.

**State:** word_id/hash, display_category, hints, hint_index, attempts,
started_by, last_guess_message_id, last_hint_at, difficulty, expires_at.

**Skor:** Doğru + zorluk + hız; deneme/ipucu cezası; timeout tek sonuç.

**Anti-cheat:** Hedef kelime callback ve log'da yoktur; ipucu hedefi açıkça
söylemez; editlenmiş eski mesaj yeniden cevap sayılmaz.

**Kabul:** Her kelimede en az üç benzersiz ipucu; Türkçe kategori doğru;
ipucu zinciri tek tek ilerler; farklı kullanıcılar state'i karıştırmaz.

**Bağımlılık:** P0 normalization, P1 vocabulary, P2 FSM, P5 score.

### 3.3 Adam Asmaca — hangman (mevcut)

**Döngü:** Harf tahminiyle görünür bilgi büyür, yanlışla can azalır; amaç
kelimeyi can bitmeden açmaktır.

**UX:** Kullanılmamış harfler keyboard'da; ipucu, kelime tahmini, durdur ve
yeni oyun butonları var. Eski callback round_id ile reddedilir.

**State:** word_hash, masked, guessed_letters, wrong_guesses, lives,
max_lives, category, difficulty, hints_used, round_id, expires_at.

**Skor:** Kalan can + hız + zorluk; yanlış ve ipucu eksi; günlük kelime
tekrarında tam ödül yok.

**Anti-cheat:** Kelime server-side; harf tek karakter normalize edilir; başka
user_id callback'le seçilemez.

**Kabul:** Türkçe harfler doğru; tekrar harf ödül vermez; timeout/win yarışta
tek sonuçtur; restart'ta maskeli tahta geri gelir.

**Bağımlılık:** P0 state, P1 vocabulary, P2 Telegram surface, P5 score.

### 3.4 Quiz / Bilgi Yarışması — quiz (mevcut)

**Döngü:** Kategori ve zorluk seçilir; seçenekli soru kısa süre içinde cevaplanır;
seri ve doğruluk toplam puanı belirler.

**UX:** Kategori → zorluk → dört seçenekli soru. 50/50, atla, seri, süre ve
sonuç butonları var. Uzun setler cursor ile ilerler.

**State:** question_set_id, question_id, answer_hash, options_hash, index,
score, streak, lifelines, answered_ids, difficulty, expires_at.

**Skor:** Doğru + hız + seri; yanlış/timeout seri reset; joker puan tavanını
düşürür; kategori leaderboard'ı ayrıdır.

**Anti-cheat:** Doğru cevap callback'te yok; soru bankası id/version ile
server-side çözülür; bankaya kaynak ve schema metadata'sı yazılır.

**Kabul:** Dört benzersiz seçenek ve tek doğru; bozuk soru yayınlanmaz; timeout
tek kez ilerler; kategori filtresi doğru bankayı seçer.

**Bağımlılık:** P0 event/state, P1 UTF-8 soru bankası, P2 pagination, P5 scores.

### 3.5 XOX / Connect-N — tictactoe (mevcut)

**Döngü:** İki oyuncu sırayla hücre seçer; 6×6, 7×7 veya 8×8 tahtada dört
aynı taş yatay, dikey veya çapraz dizilince kazanır; dolu tahta beraberliktir.

**UX:** Owner lobby'de 6/7/8 seçer; ikinci oyuncu katılır; dinamik grid,
tur, durdur, yeni oyun ve liderlik butonları görünür. Forum topic state'te
thread_id ile korunur.

**State:** round_id, chat/thread, owner, players, board_size, win_length=4,
board, current_player_id, moves, turn_expires_at, winner_id,
last_actor_message_id, schema_version.

**Skor:** Kazanan + hız/seri [PLACEHOLDER], beraberlik düşük XP; ranked ve
casual ayrılır.

**Anti-cheat:** Owner-only size, round_id, turn, occupied cell ve CAS.
Legacy 3×3 yalnız read/migration yoludur; yeni round 3×3 başlatmaz.

**Kabul:** Üç boyutta dört hiza; eski snapshot okunur; paralel hamleden biri
uygulanır; forum gönderimleri doğru thread'e gider.

**Bağımlılık:** P0 board/CAS, P2 board UI, P5 ranked, P7 load.

### 3.6 Connect Four — connect4 (mevcut)

**Döngü:** Sütun seçilir, taş aşağı düşer, dört aynı taş hizalanınca oyun biter.
Çatal kurmak ve rakibin tehdidini kapatmak temel karardır.

**UX:** 6×7 paneli sütun butonları, son hamle, vazgeç, yeniden oyna ve
liderlik. Dolu sütun disabled görünür.

**State:** board[42], players, current_player_id, last_move, move_count,
winner_id, draw, round_id, version, expires_at.

**Skor:** Galibiyet ve seri; vazgeçme ranked sonucu değildir ama seri kırılır;
oyun başı ödül [PLACEHOLDER] tavanlıdır.

**Anti-cheat:** Gravity, 0–6 sütun, oyuncu ve CAS domain'de; dolu sütun ödül
event'i üretmez.

**Kabul:** Replay aynı board; terminalde yeni hamle kilitli; restart son hamleyi
gösterir; 10 eşzamanlı round çakışmaz.

**Bağımlılık:** P0 board, P2 callback, P5 score, P7 concurrency.

### 3.7 Batak — batak (mevcut, çok turlu yükseltilecek)

**Döngü:** 2–4 kişi lobby'ye girer, ihale verir, koz seçilir ve renk takip
edilerek kart oynanır. Hedef ihaleyi tutturmak; seri maç uzun dönem stratejisidir.

**UX:** Katıl/ayrıl/başlat, ihale sayıları; kartlar yalnız sıra sahibinin
özel panelinde, masadaki trick grup panelinde; el ve maç sonucu ayrı butonlarla.

**State:** game_id, players, phase, dealer, bids, trump, private hands, trick,
tricks_won, current_turn, round_index, scores, winner_id, rng_version.

**Skor:** İhale tutarsa artı, batarsa güçlü eksi, fazla el kontrollü bonus;
coin yalnız tamamlanan maç event'inde [PLACEHOLDER].

**Anti-cheat:** Server-side deck ve commitment; private hand leak yok; renk
takibi domain kuralı; her kart action ledger'da bir kez.

**Kabul:** 2/3/4 oyuncu deterministic; yanlış renk/sıra/eski callback reddi;
restart aynı eli ve elleri korur.

**Bağımlılık:** P0 private state, P3 card engine, P5 season, P7 replay.

### 3.8 Blackjack — blackjack (mevcut)

**Döngü:** Oyuncu bahis yapar, hit/stand/double kararı verir; krupiyer kuralıyla
karşılaştırılır. Risk, daha yüksek toplam ile patlama ihtimali arasındadır.

**UX:** Bahis, hit, stand, double ve iptal; kapalı dealer kartı; terminal sonuç
ve yeniden oyna. Terminal butonlar disabled olur.

**State:** event_id, player_id, deck, player_hand, dealer_hand, bet,
charged_bet, status, base_bet, rng_version, expires_at.

**Skor:** Mevcut servis settle ve idempotent economy kullanıyor. Bahis tavanı,
günlük net kayıp ve payout [PLACEHOLDER] tek policy'de tutulmalı.

**Anti-cheat:** Deck/dealer server-side; double yalnız ilk iki kart ve bakiye
varsa; timeout sonrası settle iki ödeme yapmaz.

**Kabul:** Aynı seed replay aynı sonuç; oyuncu başkasının state'ini açamaz;
crash sonrası charged bet ile settle arasında kayıp veya çift ödeme yok.

**Bağımlılık:** P0 economy ledger, P3 card engine, P5 risk limits, P7 replay.

### 3.9 Şişe / Doğruluk-Cesaret — bottle/truth_dare (mevcut)

**Döngü:** Grup lobby'si, server-side rastgele seçim, seçilen kişinin doğruluk
veya cesaret tercihi, görev tamamlanınca yeni round. Rekabet yerine sosyal sürpriz.

**UX:** Katıl, ayrıl, çevir, doğruluk, cesaret, geç, tamamlandı ve kapat.
Görev kullanıcıya özel gönderilebilir; grup paneline hassas metin yazılmaz.

**State:** players, eligible, current_turn, prompt_id, content_pack,
consent_version, completed_count, skip_count, cooldown_until, thread_id.

**Skor:** Katılım ve görev tamamlama küçük XP; skip ve spam coin üretmez.

**Anti-cheat:** Aynı kişi arka arkaya seçilemez; random server-side; allowlist
content pack; hedefli taciz, cinsel zorlama veya kişisel veri isteyen görevler yok.

**Kabul:** Retry aynı kişiyi seçmez; ayrılan kişi tekrar seçilmez; private görev
yanlış kişiye gitmez; kapalı paket yeni görev üretmez.

**Bağımlılık:** P0 consent/state, P4 content safety, P5 social XP.

### 3.10 Rulet / Düello — roulette/duello (mevcut)

**Döngü:** Lobby, server-side chamber/round, sırayla seçim ve son kalan/düello
kazananı. Gerçek para veya fiziksel zarar yoktur; yalnız sanal oyun akışıdır.

**UX:** Katıl, başlat, sıra kimde, seçim, vazgeç, yeni round. Yalnız current
player action yapabilir; oyuncu listesi ve sıra canlı güncellenir.

**State:** game_id, phase, players, alive, current_player_id, current index,
chambers, current, round, winner_id, rng_version, expires_at.

**Skor:** Kazanana sınırlı XP; ranked rulet coin üretmez veya çok düşük sabit
ödül kullanır [PLACEHOLDER]; cooldown uygulanır.

**Anti-cheat:** Chamber secure random; seed gizli; current player ve ledger
zorunlu; moderator reset sonuç event'i üretmez.

**Kabul:** Restart sıra/chamber'ı korur; elenen pull yapamaz; retry ikinci
elenme/ödül üretmez; çoklu lobby birbirine karışmaz.

**Bağımlılık:** P0 ledger, P4 safety, P5 cap, P7 load.

### 3.11 Gomoku / Beşli — gomoku (yeni)

**Döngü:** İki kişi 10×10, 12×12 veya 15×15 tahta üzerinde beş aynı taşı
kesintisiz hizalar. Açık uçlu tehdit ve alan kontrolü XOX'tan ayrılır.

**UX:** Owner tahta boyutunu seçer; grid büyükse pencere/pagination ve son hamleyi
merkezleme kullanılır. Absolute hücre koordinatı, durdur ve yeniden oyna vardır.

**State:** board_size, win_length=5, board, current_player_id, move_count,
last_move, winning_line, round_id, window_cursor, thread_id, version.

**Skor:** Tahta boyutu ve hız çarpanı; ranked 15×15 rating, casual XP;
coin [PLACEHOLDER] günlük tavanla.

**Anti-cheat:** Occupancy, turn, CAS, line detection; client board authoritative
değil; eski panel yeni size'ı değiştiremez.

**Kabul:** Üç boyutta beşli dört yönde; pagination koordinatı kaybetmez; paralel
hamle tek sonuç; 10 round state transition p95 [PLACEHOLDER] ms.

**Bağımlılık:** P0 board, P2 large-grid UI, P5 rating, P7 load/replay.

### 3.12 Türkçe Wordle — wordle_tr (yeni)

**Döngü:** [PLACEHOLDER] harfli kelime sınırlı denemede bulunur; doğru konum,
yanlış konum ve olmayan harf renkleriyle gösterilir. Günlük puzzle ortak seed,
practice ayrı leaderboard'dır.

**UX:** Türkçe keyboard, satır grid'i, tahmin gönder, sil, ipucu ve güvenli
paylaşım. Grupta her oyuncunun tahmini kendi board'unda tutulur.

**State:** puzzle_id, date_key, answer_hash, guess_rows, keyboard_state,
attempts_left, hints_used, mode, started_at, completed_at.

**Skor:** Günlük streak ve XP; ipucu streak'i düşürür; günlük puzzle bir kez
claim edilir; practice coin üretmez veya düşük tavan kullanır.

**Anti-cheat:** Answer hash server-side; date + user unique index; callback
cevabı değil row/token hash'i taşır; cevap loglarda görünmez.

**Kabul:** Çift harf deterministic; aynı seed aynı sonuç; duplicate günlük
tamamlanmaz; ç, ğ, ı, ö, ş, ü doğru renklendirilir.

**Bağımlılık:** P1 vocabulary, P2 grid, P5 daily ledger, P7 replay.

### 3.13 Mayın Tarlası — minesweeper (yeni, solo/co-op)

**Döngü:** Hücre açılır veya bayraklanır; komşu sayıdan güvenli alan çıkarılır;
mayına basmadan board biter. Co-op'ta takım aynı board'da karar verir.

**UX:** Büyük board bölge sayfalarıyla açılır; hücre aç, bayrak, ipucu, teslim
ve yeni board. Co-op son action oyuncu etiketiyle görünür.

**State:** seed_commitment, rows, columns, mine_count, revealed, flags, phase,
players, safe_cells, elapsed_seconds, last_action_id, mode.

**Skor:** Güvenli hücre ve hız artı; yanlış flag ve mayın eksi. Co-op katkı payı
[PLACEHOLDER] idle korumasıyla bölünür.

**Anti-cheat:** Mayın yerleşimi seed commitment; board çözülmeden seed açılmaz;
action replay ve değiştirilmiş hücre reddedilir.

**Kabul:** Flood-fill ve mayın reveal doğru; co-op race'de tek puan; board
payload'ından mayın yeri çıkarılamaz; restart board'ı korur.

**Bağımlılık:** P0 secure random, P2 large-board, P5 team reward, P7 replay.

### 3.14 Amiral Battı — battleship (yeni, iki kişilik)

**Döngü:** İki oyuncu gizli grid'e gemi yerleştirir; sırayla koordinat ateşler;
isabet ve batma bilgisiyle rakip düzeni çıkarılır. Placement ve fire iki fazdır.

**UX:** Satır/sütun, yön ve onay placement butonları; fire paneli yalnız bilinen
hücreleri gösterir. Gizli board private message, ortak skor grup mesajıdır.

**State:** board_size, fleet_definition, placements_by_player, shots_by_player,
hits, sunk, phase, current_player_id, last_shot, winner_id.

**Skor:** Kazanan, isabet serisi ve hız; ranked rating ile casual XP ayrıdır;
coin [PLACEHOLDER] tavanlıdır.

**Anti-cheat:** Private board yalnız owner scope; placement commit sonrası
değişmez; rakip board callback veya update ile sızmaz; aynı koordinat tekrar
puan/turn üretmez.

**Kabul:** Gemiler çakışmaz/tahta dışına taşmaz; replay aynı isabet; gizli board
sızmaz; restart placement ve shot history'yi korur.

**Bağımlılık:** P0 private state, P2 board pagination, P5 ranked, P7 security.

### 3.15 Hafıza Eşleştirme — memory_match (yeni)

**Döngü:** İki kapalı kart açılır; eşleşirse açık kalır, değilse kapanır.
Oyuncu konum hafızası ve hız arasında seçim yapar. Duo mod sırayla oynar.

**UX:** Grid/pagination, kart aç, ipucu, geç, yeni oyun. İlk kart sonrası ikinci
seçim timer'ı; iki kart aynı mesajda kontrollü gösterilir.

**State:** deck_seed, cards, revealed, matched, pending_first, turn_user_id,
moves, matches, elapsed_seconds, mode.

**Skor:** Eşleşme ve hız artı; yanlış çift/ekstra hamle eksi; practice düşük
tavanlı; duo kazanan XP alır.

**Anti-cheat:** Kart yüzü server-side; pending_first ve timeout doğrulanır;
board payload'ı ile eşleşme id'si gönderilemez; seed replay tutulur.

**Kabul:** Aynı anda en fazla iki kart açık; timeout pending'i kapatır;
duplicate action puan üretmez; restart board ve eşleşmeleri korur.

**Bağımlılık:** P0 secure state, P2 grid UX, P5 rewards, P7 replay.

## 4. Fazlar ve bağımlılık grafiği

### P0 — Ortak platform

GameDefinition metadata'sını feature registry ile birleştir; GameSession'a
thread_id, schema sürümü, score/economy snapshot ve event referansı ekle.
Migration idempotent/rollback'lu olsun. Ledger, CAS, economy ve leaderboard
tek correlation zarfı kullansın.

**Çıkış:** Bozuk state fail-closed; duplicate callback ikinci mutation yapmıyor;
pytest, architecture ve feature audit temiz.

### P1 — Veri ve soru kalitesi

Türkçe sözlük snapshot'ı, kategori/ipuçları, checksum; quiz question_id,
kaynak, zorluk, tek doğru cevap ve açıklama schema'sı; Wordle günlük seed.

**Çıkış:** Fixture replay, duplicate/ambiguous answer ve UTF-8 testleri geçer.

### P2 — Telegram yüzeyleri

Ortak lobby, zorluk, pagination, large-board, private-view ve result kartları.
Callback'te session/round/thread doğrulaması. XOX yaklaşımı Gomoku,
Mayın Tarlası ve Hafıza'ya taşınır.

**Çıkış:** Her aksiyon butonla yapılır; eski panel yeni session'a dokunamaz.

### P3 — Kart motoru

Deck, shuffle commitment, trick, hand, suit-following, dealer ve payout
kurallarını typed modüllere çıkar; Batak ve Blackjack servislerini bağla.

**Çıkış:** Deterministic seed/replay ve private hand leak testleri.

### P4 — Sosyal içerik güvenliği

Content pack allowlist, consent, skip/cooldown; hedefli taciz, cinsel zorlama,
tehdit ve kişisel veri isteyen görevleri reddet. Rulet dili sanal ve risksiz.

### P5 — Puan, sezon ve ekonomi

GameEvent projection, sezon/lig, achievement, streak, ranked/casual ayrımı,
günlük tavan, negative balance ve admin debug exclusion.

**Çıkış:** Aynı event replay'de tek puan/coin; 100 sohbet yükünde duplicate yok.

### P6 — Yeni oyunların sırası

1. Gomoku/Beşli (board contract)
2. Türkçe Wordle (vocabulary + daily ledger)
3. Hafıza Eşleştirme (grid UI)
4. Mayın Tarlası (secure random + co-op)
5. Amiral Battı (private view + iki faz)

Her oyun önce shadow/replay, sonra owner grubu, sonra yüzde 10 canary ile açılır.
Canlı kanıt olmadan PHASE_STATUS verified yapılmaz.

### P7 — Test, gözlem ve yük

Unit: transition, malformed callback, score, timeout, hint, normalization.
Replay: seed, migration, duplicate action, restart. Integration: Mongo/Redis,
Telegram mock, private routing, forum topic. Load: 10 eşzamanlı oyun ve 100
aktif sohbet. Metric isimleri: game_started_total, game_action_total,
game_action_rejected_total, game_completed_total, game_timeout_total,
game_duplicate_action_total, game_score_event_total, game_state_recovery_total
ve game_latency_seconds.

**Çıkış:** full pytest, compileall, architecture check, feature catalog audit,
phase audit ve staging replay.

### 3.12 UNO — uno

**Döngü:** 2–8 oyuncu lobiye katılır; yedi kart dağıtılır; üst kartın rengi,
sayı/türü veya joker kuralına uyan kart oynanır. +2, +4, pas, yön değiştir,
renk seçimi, kart çekme/pas geçme ve UNO çağrısı aynı typed state geçişinden
geçer. Elini bitiren oyuncu turu kazanır ve rakip el puanları skoruna eklenir.

**UX:** Telegram panelinde lobi, oynanabilir kartlar, renk seçimleri, kart çek,
pas, UNO de ve oyunu bitir butonları vardır. Rakip elleri yalnızca kart sayısı
olarak görünür. `MiniAppGameDefinition` manifesti aynı state/action namespace'i
ile ileride React Mini App'e bağlanır.

**Kabul:** Eski callback reddi, yanlış sıra, başkasının kartı, geçersiz +4,
renk seçmeden devam, duplicate action, restart recovery ve owner/admin stop
fixture testleriyle doğrulanır.

## 5. Release checklist

- [ ] GameDefinition ve feature registry metadata'sı var.
- [ ] Türkçe metinler UTF-8 ve HTML-safe.
- [ ] Domain transition'ları handler'dan bağımsız unit testli.
- [ ] State schema, migration ve rollback tanımlı.
- [ ] Lobby, turn, result, timeout ve stop butonları var.
- [ ] Forum topic ve private-view kapsamı testli.
- [ ] Action ledger ve CAS idempotent.
- [ ] Puan/coin event'i bounded ve duplicate-safe.
- [ ] Hatalı callback güvenli hata kodu döndürüyor.
- [ ] Restart recovery ve expiry testli.
- [ ] Anti-cheat replay, rate limit ve secret/log taraması geçiyor.
- [ ] Feature catalog, data source/lisans ve PHASE_STATUS güncel.
- [ ] Owner staging grubunda gerçek buton akışı kanıtlı.

## 6. Playtest ve başlangıç tuningi

Sayılar hypothesis'tir. Aşağıdaki ölçümler aynı formatta toplanır:

| Ölçüm | Başlangıç hedefi | Kötü sinyal | Aksiyon |
|---|---:|---|---|
| İlk oyuna başlama | ≤ 20 sn [PLACEHOLDER] | Lobby bulunmuyor | Onboarding/menu düzelt |
| İlk anlamlı karar | ≤ 30 sn [PLACEHOLDER] | Oyuncu bekliyor | Buton/ipuçlarını öne çıkar |
| Callback hatası | <%1 [PLACEHOLDER] | Eski panel/retry | Round/action doğrula |
| Duplicate ödül | 0 | Ledger mismatch | Projection'ı durdur |
| Yeniden oynama | ≥%25 [PLACEHOLDER] | Erken bırakma | Loop ve ödülü test et |

Playtest'te gözlem ve yorum ayrılır: “4. turda ipucu bastı” gözlem,
“kategori anlaşılmıyor” yorumdur. Üretim ayarı yalnız tekrarlanan ölçüm ve
replay kanıtından sonra değişir.

## Changelog

- **1.0 — 2026-10-17:** Mevcut oyunlar kanıta dayalı kataloglandı; ortak
  state/event/Telegram sözleşmesi yazıldı; 10'dan fazla oyun ailesi için
  döngü, UX, state, ekonomi, anti-cheat, kabul ve faz bağımlılıkları tanımlandı;
  Gomoku, Türkçe Wordle, Mayın Tarlası, Amiral Battı ve Hafıza Eşleştirme yeni
  adaylar olarak eklendi.
