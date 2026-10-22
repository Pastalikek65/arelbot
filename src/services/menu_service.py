"""Telegram inline menus.

Menus are a presentation layer only. Every button maps to an existing
callback protocol or to an explicit help surface; no network or storage work
is performed while a menu is rendered.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.i18n import translator
from src.ai.personality import PersonalityPolicy
from src.games.catalog import WORD_GAME_CATEGORY_LABELS
from src.games.engine import DIFFICULTIES
from src.services.tictactoe_ui import tictactoe_keyboard


XOX_BOARD_SIZES = (6, 7, 8)


def tictactoe_surface_keyboard(state: dict) -> InlineKeyboardMarkup:
    """Add the round-scoped XOX board-size picker to the domain keyboard.

    The game service owns the board and move rules; this adapter only adds the
    Telegram setup controls.  Keeping the callback payload bound to the
    current round prevents an old inline panel from changing a new game.
    """
    markup = tictactoe_keyboard(state)
    rows = [list(row) for row in getattr(markup, "inline_keyboard", ())]
    if str(state.get("status") or "lobby") != "lobby":
        return InlineKeyboardMarkup(rows)
    existing = {
        str(button.callback_data)
        for row in rows
        for button in row
        if getattr(button, "callback_data", None)
    }
    round_id = str(state.get("round_id") or "legacy")
    selected = state.get("board_size")
    size_row = []
    for size in XOX_BOARD_SIZES:
        marker = "✅ " if str(selected) == str(size) else ""
        callback = f"tictactoe:{round_id}:size:{size}"
        if callback not in existing:
            size_row.append(InlineKeyboardButton(f"{marker}{size}×{size}", callback_data=callback))
    if size_row:
        rows.insert(0, size_row)
    return InlineKeyboardMarkup(rows)


class MenuService:
    def main_menu(self, language: str = "tr", user_id: int | None = None, *, chat_type: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
        text = f"{translator.t('menu.title', language)}\n\n{translator.t('menu.subtitle', language)}"
        owner = int(user_id or 0)
        group = chat_type in {"group", "supergroup"}
        if group:
            text += "\n\n<b>Bu grupta</b>\nBirlikte oyun oynayabilir, güncel bilgilere bakabilir ve grup ayarlarını yönetebilirsin. Kişisel planın ve belgelerin için botla özel sohbet aç."
        else:
            text += "\n\nBir şey sorabilir veya aşağıdan bir işlem seçebilirsin."
        keyboard = [
            [InlineKeyboardButton("🌍 Türkiye ve güncel bilgiler", callback_data="menu_turkiye")],
            [InlineKeyboardButton("🌤 Hava durumu", callback_data="weather_help"), InlineKeyboardButton("🛒 BİM aktüel", callback_data=f"bim_page:{owner}:0")],
            [InlineKeyboardButton("🤖 Yapay zekâ", callback_data="menu_ai"), InlineKeyboardButton("🎮 Oyunlar", callback_data="menu_games")],
            [InlineKeyboardButton("🏆 Liderlik tablosu", callback_data="menu_leaderboard"), InlineKeyboardButton("📣 Grup bildirimleri" if group else "🔔 Bildirimlerim", callback_data=f"notify_center:{owner}")],
            [InlineKeyboardButton("Cüzdan ve mağaza", callback_data="menu_economy")],
            [InlineKeyboardButton("📰 Haberler", callback_data="menu_news"), InlineKeyboardButton("🎨 Görsel ve ses", callback_data="menu_media")],
            [InlineKeyboardButton("🌐 Dil / Language", callback_data="menu_language"), InlineKeyboardButton("⚙️ Ayarlar", callback_data="open_ayar_menu")],
        ]
        if not group:
            keyboard.insert(4, [InlineKeyboardButton("📅 Günlük özet", callback_data=f"daily_brief:{owner}"), InlineKeyboardButton("📋 Planım", callback_data="menu_plan")])
        return text, InlineKeyboardMarkup(keyboard)

    def turkiye_menu(self, language: str = "tr", user_id: int | None = None) -> tuple[str, InlineKeyboardMarkup]:
        owner = int(user_id or 0)
        text = "🌍 <b>Türkiye ve güncel bilgiler</b>\n\nŞehir, hava, deprem ve diğer güncel bilgilere buradan ulaşabilirsin. Gelen yanıtlarda kaynağı ve veri zamanını gösteririm."
        keyboard = [
            [InlineKeyboardButton("🏙 Şehir / plaka", callback_data="city_help"), InlineKeyboardButton("🌤 Hava", callback_data="weather_help")],
            [InlineKeyboardButton("🌍 Deprem", callback_data=f"notify_earthquake:{owner}"), InlineKeyboardButton("💊 Eczane", callback_data="turkiye_pharmacy_help")],
            [InlineKeyboardButton("⛽ Akaryakıt", callback_data="turkiye_fuel_help"), InlineKeyboardButton("💱 Döviz / altın", callback_data="turkiye_finance_help")],
            [InlineKeyboardButton("📰 Haber", callback_data="menu_news"), InlineKeyboardButton("🛒 BİM aktüel", callback_data=f"bim_page:{owner}:0")],
            [InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def daily_brief_menu(self, user_id: int, preferences: dict | None = None) -> tuple[str, InlineKeyboardMarkup]:
        preferences = preferences or {}
        owner = int(user_id)
        enabled = bool(preferences.get("daily_brief_enabled"))
        city = str(preferences.get("daily_brief_city") or "İstanbul")
        time_value = str(preferences.get("daily_brief_time") or "08:00")
        state = "açık" if enabled else "kapalı"
        text = f"📅 <b>Günlük özet</b>\n\nDurum: <b>{state}</b>\nSaat: <code>{time_value}</code>\nŞehir: <b>{city}</b>\n\nAçıksa hava durumunu, son depremi, haber başlıklarını ve bugünkü not ve görevlerini tek mesajda alırsın."
        keyboard = [
            [InlineKeyboardButton("Günlük özeti kapat" if enabled else "Günlük özeti aç", callback_data=f"daily_brief:toggle:{owner}")],
            [InlineKeyboardButton("07:30", callback_data=f"daily_brief:time:{owner}:07:30"), InlineKeyboardButton("08:00", callback_data=f"daily_brief:time:{owner}:08:00"), InlineKeyboardButton("09:00", callback_data=f"daily_brief:time:{owner}:09:00")],
            [InlineKeyboardButton("🇹🇷 İstanbul", callback_data=f"daily_brief:city:{owner}:istanbul"), InlineKeyboardButton("🇹🇷 Ankara", callback_data=f"daily_brief:city:{owner}:ankara")],
            [InlineKeyboardButton("🇹🇷 İzmir", callback_data=f"daily_brief:city:{owner}:izmir"), InlineKeyboardButton("🇹🇷 Antalya", callback_data=f"daily_brief:city:{owner}:antalya")],
            [InlineKeyboardButton("🔔 Bildirim merkezi", callback_data=f"notify_center:{owner}"), InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def games_menu(self, language: str = "tr", category: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
        keyboard = [
            [InlineKeyboardButton("🎭 Anlatmaca", callback_data="game_start_anlat"), InlineKeyboardButton("🪑 Adam asmaca", callback_data="game_hangman_difficulty")],
            [InlineKeyboardButton("🔠 Kelime türetme", callback_data="game_word_categories"), InlineKeyboardButton("🎯 Bilgi yarışması", callback_data="game_start_quiz")],
            [InlineKeyboardButton("🍾 Şişe", callback_data="game_start_sise"), InlineKeyboardButton("🃏 Batak", callback_data="game_start_batak")],
            [InlineKeyboardButton("🃏 UNO · 2–8 kişi", callback_data="game_start_uno"), InlineKeyboardButton("🃏 Blackjack", callback_data="game_start_blackjack")],
            [InlineKeyboardButton("🔴 Connect Four", callback_data="game_start_connect4")],
            [InlineKeyboardButton("❌⭕ XOX", callback_data="game_start_tictactoe")],
            [InlineKeyboardButton("⚫⚪ Gomoku · Beşli", callback_data="game_start_gomoku")],
            [InlineKeyboardButton("♟️ Satranç", callback_data="game_start_chess")],
            [InlineKeyboardButton("🧞 Akinatör", callback_data="game_start_akinator")],
            [InlineKeyboardButton("🎂 Yaş Tahmini", callback_data="game_start_guess_age")],
            [InlineKeyboardButton("🔗 Kelime zinciri", callback_data="game_start_chain")],
            [InlineKeyboardButton("🎭 Takım Tabu · 4–20 kişi", callback_data="game_start_tabu")],
            [InlineKeyboardButton("Casus · 4–16 kişi", callback_data="game_start_casus"), InlineKeyboardButton("Vampir Köylü · 7–20 kişi", callback_data="game_start_vampire")],
            [InlineKeyboardButton("🔫 Rulet", callback_data="game_start_rusruleti"), InlineKeyboardButton("⚔️ Düello", callback_data="game_start_duello")],
            [InlineKeyboardButton("🏆 Liderlik", callback_data="menu_leaderboard"), InlineKeyboardButton("🛑 Oyunu bitir", callback_data="game_stop_all")],
            [InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
        ]
        groups = {
            "cards": ("Kart oyunları", "Pişti 2 veya 4, Papaz Kimde 3–8, UNO 2–8 kişiyle oynanır. Eller botun özel sohbetinde kalır. Blackjack tek kişilik oynanır.",
                      {"game_start_batak", "game_start_uno", "game_start_blackjack"}),
            "words": ("Kelime oyunları", "Bir tanımı çöz, harflerden kelime üret veya grupta zinciri sürdür.",
                      {"game_start_anlat", "game_hangman_difficulty", "game_word_categories", "game_start_chain"}),
            "boards": ("Tahta oyunları", "Satranç ve taş oyunları iki kişilik; 2048, Sudoku ve Mayın tek kişilik. Hafıza 1–4 kişiyle oynanır.",
                       {"game_start_connect4", "game_start_tictactoe", "game_start_gomoku", "game_start_chess"}),
            "guess": ("Tahmin ve bilgi", "Beş İpucu'nda erken bil, Emoji Bilmece'de çağrışımı çöz. Fotoğraf, bilgi ve mantık oyunlarını da burada bulabilirsin.",
                      {"game_start_guess_age", "game_start_akinator", "game_start_quiz"}),
            "social": ("Grup oyunları", "Tabu ve Casus 4, Vampir Köylü 7 kişiyle başlar. Gizli kart ve roller botun özel sohbetine gönderilir.",
                       {"game_start_tabu", "game_start_casus", "game_start_vampire", "game_start_sise", "game_start_duello", "game_start_rusruleti"}),
        }
        from src.games.puzzle_catalog import PUZZLES
        for puzzle in PUZZLES:
            action = "game_start_" + puzzle.key
            keyboard.append([InlineKeyboardButton(puzzle.title, callback_data=action)])
            groups[puzzle.category][2].add(action)
        if category in groups:
            title, description, actions = groups[category]
            choices = [button for row in keyboard for button in row if button.callback_data in actions]
            rows = [choices[index:index + 2] for index in range(0, len(choices), 2)]
            rows.append([InlineKeyboardButton("Cüzdan ve mağaza", callback_data="menu_economy"),
                         InlineKeyboardButton("Tüm oyun kategorileri", callback_data="menu_games")])
            return f"🎮 <b>{title}</b>\n\n{description}\n\nBir oyun seç.", InlineKeyboardMarkup(rows)
        return (
            "🎮 <b>Oyunlar</b>\n\n"
            "<b>Hızlı başlangıç</b>\nYaş Tahmini ve Kelime Zinciri için aşağıdan başlayabilirsin.\n\n"
            "Diğer oyunları türüne göre seç. Kurallar, oyuncu sayısı ve sıra oyun panelinde gösterilir.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("Yaş Tahmini", callback_data="game_start_guess_age"), InlineKeyboardButton("Kelime Zinciri", callback_data="game_start_chain")],
                [InlineKeyboardButton("Kart oyunları", callback_data="menu_games:cards"), InlineKeyboardButton("Kelime oyunları", callback_data="menu_games:words")],
                [InlineKeyboardButton("Tahta oyunları", callback_data="menu_games:boards"), InlineKeyboardButton("Tahmin ve bilgi", callback_data="menu_games:guess")],
                [InlineKeyboardButton("Grup oyunları · Tabu, Casus, Vampir", callback_data="menu_games:social")],
                [InlineKeyboardButton("Cüzdan ve mağaza", callback_data="menu_economy")],
                [InlineKeyboardButton("Liderlik tablosu", callback_data="menu_leaderboard"), InlineKeyboardButton("Oyunu bitir", callback_data="game_stop_all")],
                [InlineKeyboardButton("Ana menü", callback_data="menu_main")],
            ]),
        )

    def word_game_categories_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        """Render the category picker without starting a game yet."""

        items = list(WORD_GAME_CATEGORY_LABELS.items())
        keyboard = []
        for index in range(0, len(items), 2):
            row = []
            for key, label in items[index:index + 2]:
                row.append(
                    InlineKeyboardButton(
                        f"🧩 {label}",
                        callback_data=f"game_word_category:{key}",
                    )
                )
            keyboard.append(row)
        keyboard.append([
            InlineKeyboardButton("🎲 Rastgele", callback_data="game_start_wordgame"),
            InlineKeyboardButton("🎚️ Zorluk", callback_data="game_word_difficulty"),
        ])
        keyboard.append([
            InlineKeyboardButton("🎮 Oyun salonu", callback_data="menu_games"),
        ])
        return (
            "🧩 <b>Kelime oyunu kategorisi</b>\n\n"
            "Bir konu seç. Ardından harfleri göstereceğim. Türkçe harfleri de düz harflerle yazılmış karşılıklarını da kabul ederim.",
            InlineKeyboardMarkup(keyboard),
        )

    def word_game_difficulty_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        """Offer a button-only difficulty picker for a random word round."""

        labels = {
            "easy": "🟢 Kolay",
            "normal": "🔵 Normal",
            "hard": "🟠 Zor",
            "expert": "🔴 Uzman",
        }
        keyboard = [[
            InlineKeyboardButton(labels[key], callback_data=f"game_word_difficulty:{key}")
            for key in DIFFICULTIES
        ]]
        keyboard.append([
            InlineKeyboardButton("🧩 Kategori seç", callback_data="game_word_categories"),
            InlineKeyboardButton("🎮 Oyun salonu", callback_data="menu_games"),
        ])
        return (
            "🎚️ <b>Kelime oyunu zorluğu</b>\n\n"
            "Zorluk yükseldikçe kelimeler uzar, süre kısalır ve puan çarpanı artar.",
            InlineKeyboardMarkup(keyboard),
        )

    def hangman_difficulty_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        """Offer a button-only difficulty picker for hangman."""

        labels = {
            "easy": "🟢 Kolay",
            "normal": "🔵 Normal",
            "hard": "🟠 Zor",
            "expert": "🔴 Uzman",
        }
        keyboard = [[
            InlineKeyboardButton(labels[key], callback_data=f"game_hangman_difficulty:{key}")
            for key in DIFFICULTIES
        ]]
        keyboard.append([
            InlineKeyboardButton("🎮 Oyun salonu", callback_data="menu_games"),
            InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main"),
        ])
        return (
            "🪑 <b>Adam asmaca zorluğu</b>\n\n"
            "Zorluk yükseldikçe kelime uzar, can azalır ve puan çarpanı artar.",
            InlineKeyboardMarkup(keyboard),
        )

    def language_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        return "🌐 <b>Dil seçimi / Language</b>\n\nBot yanıtları ve menüler için dil seç.", InlineKeyboardMarkup([
            [InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr"), InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
            [InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
        ])

    def media_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        text = "🎨 <b>Görsel ve ses</b>\n\nFotoğraf yükledikten sonra analiz, yazı okuma veya sticker işlemini seç. Yeni görsel oluşturmak ya da bir video indirmek için ilgili aracı aç."
        keyboard = [
            [InlineKeyboardButton("🔎 Fotoğrafı analiz et", callback_data="media_intent:vision"), InlineKeyboardButton("📝 Fotoğraftan yazı oku", callback_data="media_intent:ocr")],
            [InlineKeyboardButton("✨ Fotoğrafı sticker yap", callback_data="media_intent:sticker"), InlineKeyboardButton("🖼 Görsel üret", callback_data="media_image_help")],
            [InlineKeyboardButton("📹 Medya indir", callback_data="help_cat_download")],
            [InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def news_menu(self, user_id: int | None = None, *, chat_type: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
        owner = int(user_id or 0)
        group = chat_type in {"group", "supergroup"}
        text = "📰 <b>Haberler</b>\n\nSon haberleri veya kısa özeti seç. Her haberde kaynağı ve yayın zamanını gösteririm."
        keyboard = [
            [InlineKeyboardButton("🗞 Son haberler", callback_data="news_latest_help"), InlineKeyboardButton("🧠 Kısa özet", callback_data="news_digest_help")],
            [InlineKeyboardButton("📣 Grup haber özeti" if group else "📡 Haber bildirimleri", callback_data=f"notify_center:{owner}" if group else f"notify_feeds:{owner}")],
            [InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def ai_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        text = "🤖 <b>Yapay zekâ</b>\n\nBir soru yazabilir, dosya, görsel veya ses gönderebilir ya da aşağıdan bir işlem seçebilirsin."
        keyboard = [
            [InlineKeyboardButton("💬 Sohbet nasıl çalışır?", callback_data="ai_chat_help"), InlineKeyboardButton("🌐 Web'de ara", callback_data="ai_web_help")],
            [InlineKeyboardButton("📄 Dosya / PDF analizi", callback_data="ai_document_help"), InlineKeyboardButton("🎨 Görsel üret", callback_data="media_image_help")],
            [InlineKeyboardButton("📖 Komut rehberi", callback_data="help_cat_ai"), InlineKeyboardButton("🎨 Medya", callback_data="menu_media")],
            [InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def plan_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        return "📋 <b>Planım</b>\n\nNotlarını, görevlerini ve hatırlatıcılarını buradan yönetebilirsin.", InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Bugünün planı", callback_data="plan_today"), InlineKeyboardButton("🗓 Haftalık plan", callback_data="plan_week")],
            [InlineKeyboardButton("📝 Notlar", callback_data="plan_notes"), InlineKeyboardButton("✅ Görevler", callback_data="plan_tasks")],
            [InlineKeyboardButton("🔔 Hatırlatıcılar", callback_data="menu_reminders")],
            [InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
        ])

    def personal_settings_menu(
        self,
        user_id: int,
        *,
        quote_enabled: bool = True,
        media_enabled: bool = True,
    ) -> tuple[str, InlineKeyboardMarkup]:
        owner = int(user_id)
        quote_label = "açık" if quote_enabled else "kapalı"
        media_label = "açık" if media_enabled else "kapalı"
        return "⚙️ <b>Kişisel ayarlar</b>\n\nÜslubumu, bildirimlerini ve hafıza tercihlerini buradan değiştirebilirsin.", InlineKeyboardMarkup([
            [InlineKeyboardButton("🎭 Arel'in üslubu", callback_data="settings_persona"), InlineKeyboardButton("🌐 Dil", callback_data="menu_language")],
            [InlineKeyboardButton(f"💬 Alıntı görseli: {quote_label}", callback_data="settings_quote_toggle"), InlineKeyboardButton(f"📥 Video indirme: {media_label}", callback_data="settings_media_toggle")],
            [InlineKeyboardButton("📅 Günlük özet", callback_data=f"daily_brief:{owner}"), InlineKeyboardButton("🔔 Bildirimler", callback_data=f"notify_center:{owner}")],
            [InlineKeyboardButton("🧠 Hafıza tercihleri", callback_data="settings_memory"), InlineKeyboardButton("🧹 Notları temizle", callback_data=f"prod:confirmclear:{owner}:notes")],
            [InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
        ])

    def group_settings_menu(self, settings: dict | None = None) -> tuple[str, InlineKeyboardMarkup]:
        """Render the administrator-only settings panel for one Telegram chat.

        Both ``/ayar`` and the inline settings button use this callback
        protocol. Values are read from the current chat, so no group setting
        can accidentally become a process-wide switch.
        """

        settings = settings or {}

        def status(key: str, default: bool = False) -> str:
            return "açık" if bool(settings.get(key, default)) else "kapalı"

        try:
            flood = max(3, min(30, int(settings.get("flood_limit", 8))))
        except (TypeError, ValueError):
            flood = 8
        next_flood = {3: 5, 5: 8, 8: 12, 12: 20, 20: 30, 30: 3}.get(flood, 8)
        persona = str(settings.get("persona_mode") or "normal")
        persona_label = PersonalityPolicy.mode_label(persona)
        keyboard = [
            [InlineKeyboardButton(f"İçerik denetimi: {status('moderation_enabled')}", callback_data="ayar_mod_toggle")],
            [InlineKeyboardButton(f"Bağlantı filtresi: {status('link_filter')}", callback_data="ayar_link_toggle")],
            [InlineKeyboardButton(f"Tekrarlanan mesaj filtresi: {status('repeat_filter')}", callback_data="ayar_repeat_toggle")],
            [InlineKeyboardButton(f"Hoş geldin: {status('welcome_enabled')}", callback_data="ayar_welcome_toggle")],
            [InlineKeyboardButton(f"Güle güle: {status('goodbye_enabled')}", callback_data="ayar_goodbye_toggle")],
            [InlineKeyboardButton(f"Tekrarlı mesajları engelle: {status('auto_ban')}", callback_data="ayar_ban_toggle")],
            [InlineKeyboardButton("🚫 Engelleme komutları", callback_data="ayar_ban_help")],
            [InlineKeyboardButton(f"Mesaj sınırı: {flood} (dokununca {next_flood})", callback_data=f"ayar_flood:{next_flood}")],
            [InlineKeyboardButton(f"Alıntı görseli (/q): {status('q_enabled', True)}", callback_data="ayar_q_toggle")],
            [InlineKeyboardButton(f"Video indirme: {status('media_enabled', True)}", callback_data="ayar_media_toggle")],
            [InlineKeyboardButton(f"Üslup: {persona_label}", callback_data="ayar_persona_cycle")],
            [InlineKeyboardButton("Menüyü kapat", callback_data="ayar_close")],
        ]
        return (
            "⚙️ <b>Grup ayarları</b>\n\n"
            "Bu ayarları yalnızca grup yöneticileri değiştirebilir. "
            "Bir ayarı değiştirmek için ilgili düğmeye dokun.\n"
            f"Üslup: <b>{persona_label}</b>",
            InlineKeyboardMarkup(keyboard),
        )

    def persona_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        return "🎭 <b>AREL ÜSLUBU</b>\n\nSohbet tonunu seç. Roast modu ayrıca açık onay ister.", InlineKeyboardMarkup([
            [InlineKeyboardButton("🤝 Normal", callback_data="persona_user:normal")],
            [InlineKeyboardButton("🔥 Roast", callback_data="persona_user:roast")],
            [InlineKeyboardButton("↩️ Ayarlara dön", callback_data="open_ayar_menu")],
        ])
