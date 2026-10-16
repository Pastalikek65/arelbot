"""Telegram inline menus.

Menus are a presentation layer only. Every button maps to an existing
callback protocol or to an explicit help surface; no network or storage work
is performed while a menu is rendered.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.i18n import translator
from src.games.catalog import WORD_GAME_CATEGORY_LABELS
from src.games.engine import DIFFICULTIES


class MenuService:
    def main_menu(self, language: str = "tr", user_id: int | None = None, *, chat_type: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
        text = f"{translator.t('menu.title', language)}\n\n{translator.t('menu.subtitle', language)}"
        owner = int(user_id or 0)
        group = chat_type in {"group", "supergroup"}
        digest_label = "📣 Grup bildirimleri" if group else "📅 Günlük özet"
        digest_callback = f"notify_center:{owner}" if group else f"daily_brief:{owner}"
        keyboard = [
            [InlineKeyboardButton("🌍 Türkiye & güncel", callback_data="menu_turkiye")],
            [InlineKeyboardButton("🌤 Hava durumu", callback_data="weather_help"), InlineKeyboardButton("🛒 BİM aktüel", callback_data=f"bim_page:{owner}:0")],
            [InlineKeyboardButton("🤖 Yapay zekâ", callback_data="menu_ai"), InlineKeyboardButton("🎮 Oyun salonu", callback_data="menu_games")],
            [InlineKeyboardButton("🏆 Liderlik", callback_data="menu_leaderboard"), InlineKeyboardButton("🔔 Bildirim merkezi", callback_data=f"notify_center:{owner}")],
            [InlineKeyboardButton(digest_label, callback_data=digest_callback), InlineKeyboardButton("📋 Planım", callback_data="menu_plan")],
            [InlineKeyboardButton("📰 Haberler", callback_data="menu_news"), InlineKeyboardButton("🎨 Görsel & ses", callback_data="menu_media")],
            [InlineKeyboardButton("🌐 Dil / Language", callback_data="menu_language"), InlineKeyboardButton("⚙️ Ayarlar", callback_data="open_ayar_menu")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def turkiye_menu(self, language: str = "tr", user_id: int | None = None) -> tuple[str, InlineKeyboardMarkup]:
        owner = int(user_id or 0)
        text = "🌍 <b>TÜRKİYE & GÜNCEL</b>\n\nŞehir, kamu bilgileri ve güncel veri araçları tek yerde. Dış kaynak yanıtlarında kaynak ve veri zamanını da gösteririm."
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
        text = f"📅 <b>GÜNLÜK ÖZET</b>\n\nDurum: <b>{state}</b>\nSaat: <code>{time_value}</code>\nŞehir: <b>{city}</b>\n\nHava, son deprem, haber başlıkları ve bugünkü not/görevleri tek mesajda yollarım."
        keyboard = [
            [InlineKeyboardButton("✅ Aç / kapat", callback_data=f"daily_brief:toggle:{owner}")],
            [InlineKeyboardButton("07:30", callback_data=f"daily_brief:time:{owner}:07:30"), InlineKeyboardButton("08:00", callback_data=f"daily_brief:time:{owner}:08:00"), InlineKeyboardButton("09:00", callback_data=f"daily_brief:time:{owner}:09:00")],
            [InlineKeyboardButton("🇹🇷 İstanbul", callback_data=f"daily_brief:city:{owner}:istanbul"), InlineKeyboardButton("🇹🇷 Ankara", callback_data=f"daily_brief:city:{owner}:ankara")],
            [InlineKeyboardButton("🇹🇷 İzmir", callback_data=f"daily_brief:city:{owner}:izmir"), InlineKeyboardButton("🇹🇷 Antalya", callback_data=f"daily_brief:city:{owner}:antalya")],
            [InlineKeyboardButton("🔔 Bildirim merkezi", callback_data=f"notify_center:{owner}"), InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def games_menu(self, language: str = "tr") -> tuple[str, InlineKeyboardMarkup]:
        text = f"{translator.t('games.title', language)}\n\n{translator.t('games.subtitle', language)}"
        keyboard = [
            [InlineKeyboardButton("🎭 Anlatmaca", callback_data="game_start_anlat"), InlineKeyboardButton("🪑 Adam asmaca", callback_data="game_hangman_difficulty")],
            [InlineKeyboardButton("🔠 Kelime türetme", callback_data="game_word_categories"), InlineKeyboardButton("🎯 Quiz", callback_data="game_start_quiz")],
            [InlineKeyboardButton("🍾 Şişe", callback_data="game_start_sise"), InlineKeyboardButton("🃏 Batak", callback_data="game_start_batak")],
            [InlineKeyboardButton("🃏 Blackjack", callback_data="game_start_blackjack")],
            [InlineKeyboardButton("🔫 Rulet", callback_data="game_start_rusruleti"), InlineKeyboardButton("⚔️ Düello", callback_data="game_start_duello")],
            [InlineKeyboardButton("🏆 Liderlik", callback_data="menu_leaderboard"), InlineKeyboardButton("🛑 Oyunu bitir", callback_data="game_stop_all")],
            [InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

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
            "🧩 <b>KELİME OYUNU KATEGORİSİ</b>\n\n"
            "Konuyu seç, harfleri ben dağıtayım. Türkçe karakterleri de ASCII yazımları da anlarım.",
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
            "🎚️ <b>KELİME OYUNU ZORLUĞU</b>\n\n"
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
            "🪑 <b>ADAM ASMACA ZORLUĞU</b>\n\n"
            "Zorluk yükseldikçe kelime uzar, can azalır ve puan çarpanı artar.",
            InlineKeyboardMarkup(keyboard),
        )

    def language_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        return "🌐 <b>Dil seçimi / Language</b>\n\nBot yanıtları ve menüler için dil seç.", InlineKeyboardMarkup([
            [InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr"), InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
            [InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
        ])

    def media_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        text = "🎨 <b>GÖRSEL & SES MERKEZİ</b>\n\nFotoğrafı kendiliğinden yorumlamam; ne istediğini butondan seçersin. Böylece bot, masadaki her şeyi üstüne alınan akraba gibi davranmaz."
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
        text = "📰 <b>HABER MERKEZİ</b>\n\nKaynağı ve zamanını göstererek haberleri getiririm; kulaktan dolma kehanet satmam."
        keyboard = [
            [InlineKeyboardButton("🗞 Son haberler", callback_data="news_latest_help"), InlineKeyboardButton("🧠 Kısa özet", callback_data="news_digest_help")],
            [InlineKeyboardButton("📣 Grup haber özeti" if group else "📡 Haber bildirimleri", callback_data=f"notify_center:{owner}" if group else f"notify_feeds:{owner}")],
            [InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def ai_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        text = "🤖 <b>YAPAY ZEKÂ MERKEZİ</b>\n\nDoğal konuş, dosya/görsel/ses gönder veya aşağıdan ne yapmak istediğini seç."
        keyboard = [
            [InlineKeyboardButton("💬 Sohbet nasıl çalışır?", callback_data="ai_chat_help"), InlineKeyboardButton("🌐 Web'de ara", callback_data="ai_web_help")],
            [InlineKeyboardButton("📄 Dosya / PDF analizi", callback_data="ai_document_help"), InlineKeyboardButton("🎨 Görsel üret", callback_data="media_image_help")],
            [InlineKeyboardButton("📖 Komut rehberi", callback_data="help_cat_ai"), InlineKeyboardButton("🎨 Medya", callback_data="menu_media")],
            [InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def plan_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        return "📋 <b>PLANIM</b>\n\nNot, görev ve hatırlatıcılarını tek panelden yönet.", InlineKeyboardMarkup([
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
        return "⚙️ <b>KİŞİSEL AYARLAR</b>\n\nÜslubumu, bildirimlerini ve hafıza tercihlerini buradan değiştir.", InlineKeyboardMarkup([
            [InlineKeyboardButton("🎭 Arel'in üslubu", callback_data="settings_persona"), InlineKeyboardButton("🌐 Dil", callback_data="menu_language")],
            [InlineKeyboardButton(f"💬 Quote: {quote_label}", callback_data="settings_quote_toggle"), InlineKeyboardButton(f"📥 Video indirici: {media_label}", callback_data="settings_media_toggle")],
            [InlineKeyboardButton("📅 Günlük özet", callback_data=f"daily_brief:{owner}"), InlineKeyboardButton("🔔 Bildirimler", callback_data=f"notify_center:{owner}")],
            [InlineKeyboardButton("🧠 Hafıza tercihleri", callback_data="settings_memory"), InlineKeyboardButton("🧹 Verileri temizle", callback_data=f"prod:confirmclear:{owner}:notes")],
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
            return "✅ AÇIK" if bool(settings.get(key, default)) else "❌ KAPALI"

        try:
            flood = max(3, min(30, int(settings.get("flood_limit", 8))))
        except (TypeError, ValueError):
            flood = 8
        next_flood = {3: 5, 5: 8, 8: 12, 12: 20, 20: 30, 30: 3}.get(flood, 8)
        persona = str(settings.get("persona_mode") or "normal")
        persona_label = {
            "normal": "Normal",
            "serious": "Ciddi",
            "balanced": "Dengeli",
            "needle": "İğneleyici",
            "roast": "Roast",
        }.get(persona, persona)
        keyboard = [
            [InlineKeyboardButton(f"Moderasyon: {status('moderation_enabled')}", callback_data="ayar_mod_toggle")],
            [InlineKeyboardButton(f"Link filtresi: {status('link_filter')}", callback_data="ayar_link_toggle")],
            [InlineKeyboardButton(f"Tekrar filtresi: {status('repeat_filter')}", callback_data="ayar_repeat_toggle")],
            [InlineKeyboardButton(f"Hoş geldin: {status('welcome_enabled')}", callback_data="ayar_welcome_toggle")],
            [InlineKeyboardButton(f"Güle güle: {status('goodbye_enabled')}", callback_data="ayar_goodbye_toggle")],
            [InlineKeyboardButton(f"Tekrarlı flood banı: {status('auto_ban')}", callback_data="ayar_ban_toggle")],
            [InlineKeyboardButton("🚫 Ban / unban kullanımı", callback_data="ayar_ban_help")],
            [InlineKeyboardButton(f"Flood limiti: {flood} (sonraki {next_flood})", callback_data=f"ayar_flood:{next_flood}")],
            [InlineKeyboardButton(f"Quote (/q): {status('q_enabled', True)}", callback_data="ayar_q_toggle")],
            [InlineKeyboardButton(f"Video indirici: {status('media_enabled', True)}", callback_data="ayar_media_toggle")],
            [InlineKeyboardButton(f"Kişilik: {persona_label}", callback_data="ayar_persona_cycle")],
            [InlineKeyboardButton("Menüyü kapat", callback_data="ayar_close")],
        ]
        return (
            "⚙️ <b>GRUP AYARLARI</b>\n\n"
            "Bu ayarlar yalnızca bu grubun yöneticileri için geçerlidir. "
            "Bir ayarı değiştirmek için ilgili düğmeye dokun.\n"
            f"Aktif Kişilik: <b>{persona_label}</b>",
            InlineKeyboardMarkup(keyboard),
        )

    def persona_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        return "🎭 <b>AREL ÜSLUBU</b>\n\nSohbet tonunu seç. Roast modu ayrıca açık onay ister.", InlineKeyboardMarkup([
            [InlineKeyboardButton("🤝 Normal / suç ortağı", callback_data="persona_user:normal")],
            [InlineKeyboardButton("🧊 Ciddi", callback_data="persona_user:serious"), InlineKeyboardButton("🙂 Dengeli", callback_data="persona_user:balanced")],
            [InlineKeyboardButton("😏 İğneleyici", callback_data="persona_user:needle"), InlineKeyboardButton("🔥 Roast / taşlama", callback_data="persona_user:roast")],
            [InlineKeyboardButton("↩️ Ayarlara dön", callback_data="open_ayar_menu")],
        ])

