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
        digest_label = "ğŸ“£ Grup bildirimleri" if group else "ğŸ“… GÃ¼nlÃ¼k Ã¶zet"
        digest_callback = f"notify_center:{owner}" if group else f"daily_brief:{owner}"
        keyboard = [
            [InlineKeyboardButton("ğŸŒ TÃ¼rkiye & gÃ¼ncel", callback_data="menu_turkiye")],
            [InlineKeyboardButton("ğŸŒ¤ Hava durumu", callback_data="weather_help"), InlineKeyboardButton("ğŸ›’ BÄ°M aktÃ¼el", callback_data=f"bim_page:{owner}:0")],
            [InlineKeyboardButton("ğŸ¤– Yapay zekÃ¢", callback_data="menu_ai"), InlineKeyboardButton("ğŸ® Oyun salonu", callback_data="menu_games")],
            [InlineKeyboardButton("ğŸ† Liderlik", callback_data="menu_leaderboard"), InlineKeyboardButton("ğŸ”” Bildirim merkezi", callback_data=f"notify_center:{owner}")],
            [InlineKeyboardButton(digest_label, callback_data=digest_callback), InlineKeyboardButton("ğŸ“‹ PlanÄ±m", callback_data="menu_plan")],
            [InlineKeyboardButton("ğŸ“° Haberler", callback_data="menu_news"), InlineKeyboardButton("ğŸ¨ GÃ¶rsel & ses", callback_data="menu_media")],
            [InlineKeyboardButton("ğŸŒ Dil / Language", callback_data="menu_language"), InlineKeyboardButton("âš™ï¸ Ayarlar", callback_data="open_ayar_menu")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def turkiye_menu(self, language: str = "tr", user_id: int | None = None) -> tuple[str, InlineKeyboardMarkup]:
        owner = int(user_id or 0)
        text = "ğŸŒ <b>TÃœRKÄ°YE & GÃœNCEL</b>\n\nÅehir, kamu bilgileri ve gÃ¼ncel veri araÃ§larÄ± tek yerde. DÄ±ÅŸ kaynak yanÄ±tlarÄ±nda kaynak ve veri zamanÄ±nÄ± da gÃ¶steririm."
        keyboard = [
            [InlineKeyboardButton("ğŸ™ Åehir / plaka", callback_data="city_help"), InlineKeyboardButton("ğŸŒ¤ Hava", callback_data="weather_help")],
            [InlineKeyboardButton("ğŸŒ Deprem", callback_data=f"notify_earthquake:{owner}"), InlineKeyboardButton("ğŸ’Š Eczane", callback_data="turkiye_pharmacy_help")],
            [InlineKeyboardButton("â›½ AkaryakÄ±t", callback_data="turkiye_fuel_help"), InlineKeyboardButton("ğŸ’± DÃ¶viz / altÄ±n", callback_data="turkiye_finance_help")],
            [InlineKeyboardButton("ğŸ“° Haber", callback_data="menu_news"), InlineKeyboardButton("ğŸ›’ BÄ°M aktÃ¼el", callback_data=f"bim_page:{owner}:0")],
            [InlineKeyboardButton("ğŸ  Ana menÃ¼", callback_data="menu_main")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def daily_brief_menu(self, user_id: int, preferences: dict | None = None) -> tuple[str, InlineKeyboardMarkup]:
        preferences = preferences or {}
        owner = int(user_id)
        enabled = bool(preferences.get("daily_brief_enabled"))
        city = str(preferences.get("daily_brief_city") or "Ä°stanbul")
        time_value = str(preferences.get("daily_brief_time") or "08:00")
        state = "aÃ§Ä±k" if enabled else "kapalÄ±"
        text = f"ğŸ“… <b>GÃœNLÃœK Ã–ZET</b>\n\nDurum: <b>{state}</b>\nSaat: <code>{time_value}</code>\nÅehir: <b>{city}</b>\n\nHava, son deprem, haber baÅŸlÄ±klarÄ± ve bugÃ¼nkÃ¼ not/gÃ¶revleri tek mesajda yollarÄ±m."
        keyboard = [
            [InlineKeyboardButton("âœ… AÃ§ / kapat", callback_data=f"daily_brief:toggle:{owner}")],
            [InlineKeyboardButton("07:30", callback_data=f"daily_brief:time:{owner}:07:30"), InlineKeyboardButton("08:00", callback_data=f"daily_brief:time:{owner}:08:00"), InlineKeyboardButton("09:00", callback_data=f"daily_brief:time:{owner}:09:00")],
            [InlineKeyboardButton("ğŸ‡¹ğŸ‡· Ä°stanbul", callback_data=f"daily_brief:city:{owner}:istanbul"), InlineKeyboardButton("ğŸ‡¹ğŸ‡· Ankara", callback_data=f"daily_brief:city:{owner}:ankara")],
            [InlineKeyboardButton("ğŸ‡¹ğŸ‡· Ä°zmir", callback_data=f"daily_brief:city:{owner}:izmir"), InlineKeyboardButton("ğŸ‡¹ğŸ‡· Antalya", callback_data=f"daily_brief:city:{owner}:antalya")],
            [InlineKeyboardButton("ğŸ”” Bildirim merkezi", callback_data=f"notify_center:{owner}"), InlineKeyboardButton("ğŸ  Ana menÃ¼", callback_data="menu_main")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def games_menu(self, language: str = "tr") -> tuple[str, InlineKeyboardMarkup]:
        text = f"{translator.t('games.title', language)}\n\n{translator.t('games.subtitle', language)}"
        keyboard = [
            [InlineKeyboardButton("ğŸ­ Anlatmaca", callback_data="game_start_anlat"), InlineKeyboardButton("ğŸª‘ Adam asmaca", callback_data="game_hangman_difficulty")],
            [InlineKeyboardButton("ğŸ”  Kelime tÃ¼retme", callback_data="game_word_categories"), InlineKeyboardButton("ğŸ¯ Quiz", callback_data="game_start_quiz")],
            [InlineKeyboardButton("ğŸ¾ ÅiÅŸe", callback_data="game_start_sise"), InlineKeyboardButton("ğŸƒ Batak", callback_data="game_start_batak")],
            [InlineKeyboardButton("ğŸƒ Blackjack", callback_data="game_start_blackjack")],
            [InlineKeyboardButton("ğŸ”« Rulet", callback_data="game_start_rusruleti"), InlineKeyboardButton("âš”ï¸ DÃ¼ello", callback_data="game_start_duello")],
            [InlineKeyboardButton("ğŸ† Liderlik", callback_data="menu_leaderboard"), InlineKeyboardButton("ğŸ›‘ Oyunu bitir", callback_data="game_stop_all")],
            [InlineKeyboardButton("ğŸ  Ana menÃ¼", callback_data="menu_main")],
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
                        f"ğŸ§© {label}",
                        callback_data=f"game_word_category:{key}",
                    )
                )
            keyboard.append(row)
        keyboard.append([
            InlineKeyboardButton("ğŸ² Rastgele", callback_data="game_start_wordgame"),
            InlineKeyboardButton("ğŸšï¸ Zorluk", callback_data="game_word_difficulty"),
        ])
        keyboard.append([
            InlineKeyboardButton("ğŸ® Oyun salonu", callback_data="menu_games"),
        ])
        return (
            "ğŸ§© <b>KELÄ°ME OYUNU KATEGORÄ°SÄ°</b>\n\n"
            "Konuyu seÃ§, harfleri ben daÄŸÄ±tayÄ±m. TÃ¼rkÃ§e karakterleri de ASCII yazÄ±mlarÄ± da anlarÄ±m.",
            InlineKeyboardMarkup(keyboard),
        )

    def word_game_difficulty_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        """Offer a button-only difficulty picker for a random word round."""

        labels = {
            "easy": "ğŸŸ¢ Kolay",
            "normal": "ğŸ”µ Normal",
            "hard": "ğŸŸ  Zor",
            "expert": "ğŸ”´ Uzman",
        }
        keyboard = [[
            InlineKeyboardButton(labels[key], callback_data=f"game_word_difficulty:{key}")
            for key in DIFFICULTIES
        ]]
        keyboard.append([
            InlineKeyboardButton("ğŸ§© Kategori seÃ§", callback_data="game_word_categories"),
            InlineKeyboardButton("ğŸ® Oyun salonu", callback_data="menu_games"),
        ])
        return (
            "ğŸšï¸ <b>KELÄ°ME OYUNU ZORLUÄU</b>\n\n"
            "Zorluk yÃ¼kseldikÃ§e kelimeler uzar, sÃ¼re kÄ±salÄ±r ve puan Ã§arpanÄ± artar.",
            InlineKeyboardMarkup(keyboard),
        )

    def hangman_difficulty_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        """Offer a button-only difficulty picker for hangman."""

        labels = {
            "easy": "ğŸŸ¢ Kolay",
            "normal": "ğŸ”µ Normal",
            "hard": "ğŸŸ  Zor",
            "expert": "ğŸ”´ Uzman",
        }
        keyboard = [[
            InlineKeyboardButton(labels[key], callback_data=f"game_hangman_difficulty:{key}")
            for key in DIFFICULTIES
        ]]
        keyboard.append([
            InlineKeyboardButton("ğŸ® Oyun salonu", callback_data="menu_games"),
            InlineKeyboardButton("ğŸ  Ana menÃ¼", callback_data="menu_main"),
        ])
        return (
            "ğŸª‘ <b>ADAM ASMACA ZORLUÄU</b>\n\n"
            "Zorluk yÃ¼kseldikÃ§e kelime uzar, can azalÄ±r ve puan Ã§arpanÄ± artar.",
            InlineKeyboardMarkup(keyboard),
        )

    def language_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        return "ğŸŒ <b>Dil seÃ§imi / Language</b>\n\nBot yanÄ±tlarÄ± ve menÃ¼ler iÃ§in dil seÃ§.", InlineKeyboardMarkup([
            [InlineKeyboardButton("ğŸ‡¹ğŸ‡· TÃ¼rkÃ§e", callback_data="lang_tr"), InlineKeyboardButton("ğŸ‡¬ğŸ‡§ English", callback_data="lang_en")],
            [InlineKeyboardButton("ğŸ  Ana menÃ¼", callback_data="menu_main")],
        ])

    def media_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        text = "ğŸ¨ <b>GÃ–RSEL & SES MERKEZÄ°</b>\n\nFotoÄŸrafÄ± kendiliÄŸinden yorumlamam; ne istediÄŸini butondan seÃ§ersin. BÃ¶ylece bot, masadaki her ÅŸeyi Ã¼stÃ¼ne alÄ±nan akraba gibi davranmaz."
        keyboard = [
            [InlineKeyboardButton("ğŸ” FotoÄŸrafÄ± analiz et", callback_data="media_intent:vision"), InlineKeyboardButton("ğŸ“ FotoÄŸraftan yazÄ± oku", callback_data="media_intent:ocr")],
            [InlineKeyboardButton("âœ¨ FotoÄŸrafÄ± sticker yap", callback_data="media_intent:sticker"), InlineKeyboardButton("ğŸ–¼ GÃ¶rsel Ã¼ret", callback_data="media_image_help")],
            [InlineKeyboardButton("ğŸ“¹ Medya indir", callback_data="help_cat_download")],
            [InlineKeyboardButton("ğŸ  Ana menÃ¼", callback_data="menu_main")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def news_menu(self, user_id: int | None = None, *, chat_type: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
        owner = int(user_id or 0)
        group = chat_type in {"group", "supergroup"}
        text = "ğŸ“° <b>HABER MERKEZÄ°</b>\n\nKaynaÄŸÄ± ve zamanÄ±nÄ± gÃ¶stererek haberleri getiririm; kulaktan dolma kehanet satmam."
        keyboard = [
            [InlineKeyboardButton("ğŸ— Son haberler", callback_data="news_latest_help"), InlineKeyboardButton("ğŸ§  KÄ±sa Ã¶zet", callback_data="news_digest_help")],
            [InlineKeyboardButton("ğŸ“£ Grup haber Ã¶zeti" if group else "ğŸ“¡ Haber bildirimleri", callback_data=f"notify_center:{owner}" if group else f"notify_feeds:{owner}")],
            [InlineKeyboardButton("ğŸ  Ana menÃ¼", callback_data="menu_main")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def ai_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        text = "ğŸ¤– <b>YAPAY ZEKÃ‚ MERKEZÄ°</b>\n\nDoÄŸal konuÅŸ, dosya/gÃ¶rsel/ses gÃ¶nder veya aÅŸaÄŸÄ±dan ne yapmak istediÄŸini seÃ§."
        keyboard = [
            [InlineKeyboardButton("ğŸ’¬ Sohbet nasÄ±l Ã§alÄ±ÅŸÄ±r?", callback_data="ai_chat_help"), InlineKeyboardButton("ğŸŒ Web'de ara", callback_data="ai_web_help")],
            [InlineKeyboardButton("ğŸ“„ Dosya / PDF analizi", callback_data="ai_document_help"), InlineKeyboardButton("ğŸ¨ GÃ¶rsel Ã¼ret", callback_data="media_image_help")],
            [InlineKeyboardButton("ğŸ“– Komut rehberi", callback_data="help_cat_ai"), InlineKeyboardButton("ğŸ¨ Medya", callback_data="menu_media")],
            [InlineKeyboardButton("ğŸ  Ana menÃ¼", callback_data="menu_main")],
        ]
        return text, InlineKeyboardMarkup(keyboard)

    def plan_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        return "ğŸ“‹ <b>PLANIM</b>\n\nNot, gÃ¶rev ve hatÄ±rlatÄ±cÄ±larÄ±nÄ± tek panelden yÃ¶net.", InlineKeyboardMarkup([
            [InlineKeyboardButton("ğŸ“… BugÃ¼nÃ¼n planÄ±", callback_data="plan_today"), InlineKeyboardButton("ğŸ—“ HaftalÄ±k plan", callback_data="plan_week")],
            [InlineKeyboardButton("ğŸ“ Notlar", callback_data="plan_notes"), InlineKeyboardButton("âœ… GÃ¶revler", callback_data="plan_tasks")],
            [InlineKeyboardButton("ğŸ”” HatÄ±rlatÄ±cÄ±lar", callback_data="menu_reminders")],
            [InlineKeyboardButton("ğŸ  Ana menÃ¼", callback_data="menu_main")],
        ])

    def personal_settings_menu(
        self,
        user_id: int,
        *,
        quote_enabled: bool = True,
        media_enabled: bool = True,
    ) -> tuple[str, InlineKeyboardMarkup]:
        owner = int(user_id)
        quote_label = "aÃ§Ä±k" if quote_enabled else "kapalÄ±"
        media_label = "aÃ§Ä±k" if media_enabled else "kapalÄ±"
        return "âš™ï¸ <b>KÄ°ÅÄ°SEL AYARLAR</b>\n\nÃœslubumu, bildirimlerini ve hafÄ±za tercihlerini buradan deÄŸiÅŸtir.", InlineKeyboardMarkup([
            [InlineKeyboardButton("ğŸ­ Arel'in Ã¼slubu", callback_data="settings_persona"), InlineKeyboardButton("ğŸŒ Dil", callback_data="menu_language")],
            [InlineKeyboardButton(f"ğŸ’¬ Quote: {quote_label}", callback_data="settings_quote_toggle"), InlineKeyboardButton(f"ğŸ“¥ Video indirici: {media_label}", callback_data="settings_media_toggle")],
            [InlineKeyboardButton("ğŸ“… GÃ¼nlÃ¼k Ã¶zet", callback_data=f"daily_brief:{owner}"), InlineKeyboardButton("ğŸ”” Bildirimler", callback_data=f"notify_center:{owner}")],
            [InlineKeyboardButton("ğŸ§  HafÄ±za tercihleri", callback_data="settings_memory"), InlineKeyboardButton("ğŸ§¹ Verileri temizle", callback_data=f"prod:confirmclear:{owner}:notes")],
            [InlineKeyboardButton("ğŸ  Ana menÃ¼", callback_data="menu_main")],
        ])

    def persona_menu(self) -> tuple[str, InlineKeyboardMarkup]:
        return "ğŸ­ <b>AREL ÃœSLUBU</b>\n\nSohbet tonunu seÃ§. Roast modu ayrÄ±ca aÃ§Ä±k onay ister.", InlineKeyboardMarkup([
            [InlineKeyboardButton("ğŸ¤ Normal / suÃ§ ortaÄŸÄ±", callback_data="persona_user:normal")],
            [InlineKeyboardButton("ğŸ§Š Ciddi", callback_data="persona_user:serious"), InlineKeyboardButton("ğŸ™‚ Dengeli", callback_data="persona_user:balanced")],
            [InlineKeyboardButton("ğŸ˜ Ä°ÄŸneleyici", callback_data="persona_user:needle"), InlineKeyboardButton("ğŸ”¥ Roast / taÅŸlama", callback_data="persona_user:roast")],
            [InlineKeyboardButton("â†©ï¸ Ayarlara dÃ¶n", callback_data="open_ayar_menu")],
        ])
