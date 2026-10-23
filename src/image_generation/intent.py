"""Recognize explicit Turkish image requests without trusting model tool prose."""
from __future__ import annotations

import re


_SUBJECT = re.compile(r"\b(?:görsel(?:i|ini)?|res(?:im|mi|mini)|fotoğraf(?:ı|ını)?|çizim(?:i|ini)?)\b")
_ACTION = re.compile(r"\b(?:oluştur|üret|çiz)(?:[ae]bilir|ur|ir|er|sene|sana)?\b")
_NOT_A_REQUEST = re.compile(
    r"\b(?:oluşturma|üretme|çizme|istemiyorum|istemedim|istemem|vazgeç|"
    r"nasıl|neden|niçin|komutunu|komutu|kodu|json|dedi|demiş|hakkında|anlat|çevir|söyle)\b"
)


def explicit_image_prompt(text: str | None) -> str | None:
    """Return the human's bounded prompt only for a direct generation request.

    Negative requests and discussion of a command stay in conversation. This
    function runs after normal Telegram admission/mention/moderation checks.
    Queue validation, per-user limits and deduplication remain in the service.
    """
    value = " ".join(str(text or "").split())
    normalized = value.casefold().replace("i\u0307", "i")
    if not value or len(value) > 4000 or value.startswith("/"):
        return None
    if re.search(r"[\"'”’]\s+yaz[.!?]*$", normalized):
        return None
    if _NOT_A_REQUEST.search(normalized) or not _SUBJECT.search(normalized) or not _ACTION.search(normalized):
        return None
    return re.sub(r"^(?:(?:@\w+|arel)[,:]?\s+)+", "", value, flags=re.IGNORECASE).strip()
