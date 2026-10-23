"""Small, original Turkish phrase bank for the explicitly selected Roast mode."""

from __future__ import annotations

import hashlib
import random
import re
from collections import Counter

ROAST_PHRASES = (
    "sikindirik plan", "sikik bahane", "siktir et", "boktan fikir",
    "bokunu çıkarmak", "sıçıp sıvamak", "götünden uydurmak", "götü tutuşmak",
    "boku yemek", "götü başı dağıtmak", "siktir çekmek", "sik gibi kalmak",
    "bok çukuruna düşmek", "boktan döngü", "sikik tesadüf", "sıçtığının resmi",
    "göt kadar plan", "yarrağı yemek", "bok gibi batmak", "götüyle düşünmek",
    "siktir olup gitmek", "boktan sürpriz", "sikimsonik bahane", "boka saplanmak",
    "paçaları sıvayıp boka basmak", "götüyle inatlaşmak", "boktan hesap",
    "sik gibi ortada kalmak", "bokunu çıkarmadan bırakmak", "sıçışın kitabını yazmak",
)

ROAST_SAYINGS = (
    "acele işe şeytan karışır, sen şeytanın da bokunu çıkarmışsın",
    "dereyi görmeden paçayı sıvama, sonra bokun içinde kalırsın",
    "taşıma suyla değirmen dönmez, erteleme bokuyla da iş yetişmez",
    "kaş yapayım derken göz çıkardın, şimdi sıçıp sıvama",
    "damlaya damlaya göl olur, bahane damlaya damlaya bok çukuru",
    "ayağını yorganına göre uzat, yoksa plan götünden patlar",
    "eğri oturup doğru konuşalım, bu hesap bok gibi",
    "sakla samanı gelir zamanı, sakla bahaneyi gelir sıçış anı",
)

_STYLE_OBJECTION = re.compile(
    r"\b(?:böyle\s+konuşma|küfür\s+etme|normal\s+konuş|daha\s+sakin\s+konuş|roast\s+yapma)\b",
    re.IGNORECASE,
)
_CAPITULATION = re.compile(
    r"^\s*(?:anladım|tamam|haklısın|özür\s+dilerim|kusura\s+bakma)[,!. ]*"
    r"(?:daha\s+sakin|normal\s+konuş|küfür\s+etmeyeceğim|böyle\s+konuşmayacağım|"
    r"saygılı\s+bir\s+dille)",
    re.IGNORECASE,
)
_PROFANITY = re.compile(r"\b(?:sik\w*|bok\w*|göt\w*|sıç\w*|yarra\w*|amk)\b", re.IGNORECASE)
_AWKWARD_COMPOUND = re.compile(r"siktiri?bok", re.IGNORECASE)


def strip_roast_capitulation(response: str) -> str:
    """Remove only a leading promise to abandon the already selected style."""

    value = str(response or "").strip()
    if _CAPITULATION.search(value) and "." in value[:160]:
        remainder = value.split(".", 1)[1].strip()
        return remainder or value
    return value


def roast_needs_repair(user_text: str, response: str) -> bool:
    """Detect an explicit surrender of the selected Roast tone, not all mild turns."""

    value = str(response or "").strip()
    if not value:
        return False
    stems = Counter("bok" if word.casefold().startswith("bok") else word.casefold()
                    for word in _PROFANITY.findall(value))
    return bool(_CAPITULATION.search(value) or
                _AWKWARD_COMPOUND.search(value) or
                any(count > 2 for count in stems.values()) or
                _STYLE_OBJECTION.search(str(user_text or "")) and not _PROFANITY.search(value))


def roast_repertoire_instruction(user_text: str, recent_turns: int = 0) -> str:
    """Select varied examples without remembering text or changing global state."""

    key = f"{max(0, int(recent_turns))}:{str(user_text or '')[:4096]}".encode()
    seed = int.from_bytes(hashlib.sha256(key).digest()[:8], "big")
    rng = random.Random(seed)
    phrases = rng.sample(ROAST_PHRASES, 3)
    saying = rng.choice(ROAST_SAYINGS)
    return (
        "ROAST REPERTUVARI: Bu tur için olası kelime/deyim aileleri: "
        + "; ".join(phrases) + ". Atasözü uyarlaması ritmi: " + saying + ". "
        "Bunları aynen ezberden tekrar etme; kullanıcının anlattığı duruma uyan özgün ve kısa bir taşlamaya dönüştür. "
        "Gerçek bir atasözü diye sunma."
    )
