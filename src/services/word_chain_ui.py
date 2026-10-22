"""Telegram-safe presentation for the word-chain domain state."""

from __future__ import annotations

from html import escape
import math
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from src.games.word_chain import TURN_SECONDS


def word_chain_error(code: str) -> str:
    return {
        "word_chain_not_found": "Açık bir Kelime Zinciri oyunu yok. /zincir ile yeni oyun aç.",
        "word_chain_not_your_turn": "Şu anda sıra sende değil.",
        "word_chain_stale_turn": "Bu düğme eski sıraya ait. Güncel paneli kullan.",
        "word_chain_stale_round": "Bu düğme eski bir oyuna ait. /zincir ile güncel oyunu aç.",
        "word_chain_not_owner": "Bu işlemi oyunu kuran kişi yapabilir.",
        "word_chain_need_players": "Başlamak için en az iki oyuncu katılmalı.",
        "word_chain_lobby_closed": "Bu oyun yeni katılıma kapalı. /zincir ile güncel oyunu aç.",
        "word_chain_already_joined": "Bu oyuna zaten katıldın.",
        "word_chain_not_member": "Bu oyunda yer almıyorsun. Yeni oyun açıldığında Katıl düğmesini kullan.",
        "word_chain_no_passes": "İki pas hakkını da kullandın. Sıranı tamamlamak için bir kelime yaz.",
        "word_chain_turn_expired": "Sıra süresi doldu; panel birazdan yenilenecek.",
        "word_chain_scores_pending": "Maç puanları kaydediliyor; biraz sonra tekrar dene.",
    }.get(code, "Bu işlem şu anda yapılamıyor. Güncel oyun panelini kullan.")


def render_word_chain(state: dict, *, notice: str | None = None, now: float | None = None) -> str:
    players = state["players"]
    scores = state["scores"]
    lines = ["🔗 <b>Kelime Zinciri</b>"]
    if notice:
        lines.append(escape(notice))
    lines.append("")
    lines.append(" · ".join(
        f"{escape(str(player['name']))}: {int(scores[str(player['id'])])}"
        for player in players
    ))
    phase = state["phase"]
    if phase == "lobby":
        lines.extend((
            "", "<b>Oyuncular bekleniyor</b>",
            "2–20 kişi · Kişi başı 5 hamle · Her hamle 30 saniye · 2 pas hakkı",
            "", "Katıl düğmesine bas. En az iki kişi hazır olduğunda oyunu kuran kişi maçı başlatabilir.",
            "Sıran geldiğinde önceki kelimenin son harfiyle başlayan bir kelimeyi doğrudan gruba yaz.",
            "En az üç harf kullan. Özel adlar ve kısaltmalar kabul edilmez; Türkçe harfler ayrı değerlendirilir.",
            "Her harf 1 puan. Yeni kelime kalmazsa zincir yeniden başlar; maç devam eder.",
        ))
    elif phase == "playing":
        current = players[int(state["turn_index"])]
        last = state.get("last_word")
        lines.extend(("", f"Sıra: <b>{escape(str(current['name']))}</b>"))
        if last:
            lines.append(f"Son kelime: <b>{escape(str(last))}</b> · <b>{escape(str(last[-1]))}</b> ile başla")
        else:
            lines.append("İlk kelimeyi sen seç. En az üç harfli bir Türkçe kelime yaz.")
        remaining = max(0, math.ceil(float(state.get("turn_expires_at", 0)) - (time.time() if now is None else now)))
        lines.append(f"Hamle: {int(state['turn_count']) + 1}/{int(state.get('max_turns') or len(players) * 5)} · Kalan süre: {min(TURN_SECONDS, remaining)} saniye")
        lines.append(f"Pas hakkı: {int(state.get('passes', {}).get(str(current['id']), 2))}")
        lines.append("Kelimeyi doğrudan gruba yaz; komut kullanman gerekmez.")
        if state.get("used_words"):
            lines.append("Son kelimeler: " + " → ".join(escape(word) for word in state["used_words"][-6:]))
    elif phase == "finished":
        names = [escape(str(player["name"])) for player in players if int(player["id"]) in state["winner_ids"]]
        lines.extend(("", "🏆 Kazanan: " + ", ".join(names) if names else "Bu maçta geçerli kelime yazılmadı; kazanan yok.", "Yeni oyun: <code>/zincir</code>"))
    else:
        lines.extend(("", "Oyun iptal edildi. Bu maç için puan ve jeton verilmez.", "Yeni oyun: <code>/zincir</code>"))
    return "\n".join(lines)


def word_chain_keyboard(state: dict) -> InlineKeyboardMarkup:
    prefix = f"chain:{state['round_id']}:{int(state['turn_count'])}"
    def button(label, action):
        return InlineKeyboardButton(label, callback_data=f"{prefix}:{action}")
    if state["phase"] == "lobby":
        return InlineKeyboardMarkup([
            [button("Katıl", "join"), button("Ayrıl", "leave")],
            [button("Maçı başlat", "start"), button("Oyunu bitir", "stop")],
        ])
    if state["phase"] == "playing":
        return InlineKeyboardMarkup([
            [button("Pas geç", "pass"), button("Yenile", "refresh")],
            [button("Oyunu bitir", "stop")],
        ])
    return InlineKeyboardMarkup([[InlineKeyboardButton("Yeni oyun", callback_data="game_start_chain")]])
