from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
USER_SURFACE_FILES = tuple(sorted((ROOT / "src" / "handlers").glob("*.py")))

# These are common display-text regressions. Command aliases such as
# ``/doviz`` remain intentionally ASCII; the audit inspects complete user
# phrases instead of banning valid protocol identifiers.
FORBIDDEN_DISPLAY_FRAGMENTS = (
    "Kullanim:",
    "Lutfen",
    "alinamadi",
    "su an alinamiyor",
    "kullanilamiyor",
    "Ucak bileti",
    "Akaryakit verileri",
    "Doviz kurlari",
    "Bim firsatlari",
    "nobetci eczane",
    "Hikaye olustur",
    "Gorev servisi",
    "Acik gorev",
    "Hatirlatici servisi",
    "Bekleyen hatirlatici",
    "Gecersiz sticker",
    "Gecersiz islem",
    "Islem iptal",
    "hazir degil",
    "baska bir kullanici",
    "onayinin suresi",
    "dosyasi bulunamadi",
    "dosyasi gecersiz",
    "sana ait degil",
    "artik mevcut degil",
    "silinmis olabilir",
    "Not bulunamadi",
    "kalici",
    "Kucuk resim",
    "Kücük resim",
    "formati desteklenmiyor",
    "Sticker'i",
    "Iptal",
    "kayip bir kedi",
    "dosyasi islenemedi",
    "Hatirlaticiyi iptal et",
    "paketi icin kucuk resim guncellendi",
    "Paket ismi sadece",
    "sticker'i",
    "yanit vererek",
    "silmek icin",
)


def _string_constants(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def test_user_facing_handler_text_keeps_turkish_diacritics():
    failures = []
    for path in USER_SURFACE_FILES:
        for line, value in _string_constants(path):
            matched = [fragment for fragment in FORBIDDEN_DISPLAY_FRAGMENTS if fragment in value]
            if matched:
                failures.append(f"{path.relative_to(ROOT)}:{line}:{','.join(matched)}")
    assert failures == []
