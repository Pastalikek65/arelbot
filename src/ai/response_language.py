"""Detect unexpected scripts in Turkish prose without corrupting code or sources."""

from __future__ import annotations

import re
import unicodedata

LANGUAGE_INSTRUCTION = (
    "YANIT DİLİ: Açıklamaların tamamını doğal Türkçe yaz. Başka dilden rastgele sözcük, "
    "bağlaç veya kapanış ekleme. Kod, bağlantı, özel ad, kullanıcının alıntısı ve açıkça "
    "istenen yabancı dilde çeviri/metin bu kuralın dışındadır. Göndermeden önce dili kontrol et."
)

LANGUAGE_FAILURE_RESPONSE = "Yanıtı düzgün Türkçe olarak tamamlayamadım; yeniden deneyelim."

_PROTECTED = re.compile(r"```[\s\S]*?```|`[^`\n]+`|https?://[^\s<>]+")
_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)
_LANGUAGES = re.compile(
    r"\b(?:türkçe|ingilizce|almanca|fransızca|ispanyolca|italyanca|rusça|çince|japonca|"
    r"korece|arapça|farsça|yunanca|ibranice|hintçe|portekizce)(?:ye|ya|nin|nın|ni|yi|"
    r"si|sı|sını|sini|sına|sine|den|dan)?\b",
    re.IGNORECASE,
)
_TRANSLATION = re.compile(r"\b(?:çevir|tercüme|yaz|söyle|yanıt|cevap)(?!ma|me)", re.IGNORECASE)
_LANGUAGE_PROHIBITION = re.compile(
    r"^\s*(?:(?:kelime|sözcük|karakter|ifade|metin|harf|yanıt|cevap)\s+){0,2}"
    r"(?:ekleme|kullanma|istemiyorum|yazma|konuşma|karıştırma)\b",
    re.IGNORECASE,
)


def _foreign_output_requested(user_text: str) -> bool:
    choices = list(_LANGUAGES.finditer(user_text))
    if not choices:
        return False
    last = choices[-1]
    language_form = last.group().casefold()
    if _LANGUAGE_PROHIBITION.search(user_text[last.end():]):
        return False
    implicit_translation = language_form.endswith(("sı", "si")) and re.search(
        r"nedir|ne demek", user_text[last.end():], re.IGNORECASE
    )
    if not _TRANSLATION.search(user_text) and not implicit_translation:
        return False
    # In "Rusça metni Türkçeye çevir", Turkish is the target, not Russian.
    return not language_form.startswith("türkçe")


def has_unexpected_script(text: str, user_text: str = "", source_text: str = "") -> bool:
    """Flag foreign-script prose, allowing explicit translations and source terms.

    This is a script consistency check, not a general language classifier:
    Latin-script loanwords and technical identifiers are left intact.
    """

    if _foreign_output_requested(user_text):
        return False
    prose = _PROTECTED.sub(" ", text)
    for match in _WORDS.finditer(prose):
        word = match.group()
        if not any(char.isalpha() and "LATIN" not in unicodedata.name(char, "") for char in word):
            continue
        if word in user_text or word in source_text:
            continue
        return True
    return False
