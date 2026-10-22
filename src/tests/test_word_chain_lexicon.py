"""Offline Turkish word-chain vocabulary acceptance checks."""

import hashlib
import json
from pathlib import Path

import pytest

from src.games.word_chain import LEXICON, _word
from src.games.turkish_words import ORIGINAL_WORDS, WORDS, canonical_word


COMMON_WORDS = """
selam alem ayakkabı orijinal sandal havuz pencere bilgisayar kalem kutu masa
telefon araba tren uçak havaalanı otobüs terminal vapur deniz yağmur kar
gökkuşağı sıcak soğuk güzel kötü hızlı yavaş kırmızı mavi yeşil sarı beyaz
siyah mor pembe kahverengi turuncu apartman sokak cadde meydan şehir köy
mahalle okul öğretmen öğrenci kitap defter silgi çanta sınav ders tarih
matematik fizik kimya biyoloji müzik resim spor futbol basketbol voleybol
tenis koşu elbise gömlek pantolon etek çorap şapka mont ceket kazak aile
anne baba kardeş çocuk bebek arkadaş komşu yemek çorba pilav ekmek peynir
yoğurt süt yumurta meyve sebze elma armut muz çilek kiraz karpuz kavun üzüm
domates salatalık biber patates havuç soğan sarımsak limon portakal mandalina
çay kahve şeker tuz bıçak kaşık çatal tabak bardak tencere tava fırın ocak
buzdolabı çamaşır makine kapı anahtar duvar çatı merdiven asansör bahçe ağaç
çiçek yaprak kök dal toprak taş kum kaya nehir dere göl ada orman hayvan kedi
köpek kuş balık inek koyun keçi tavuk horoz ördek arı karınca kelebek böcek
sinek
""".split()


@pytest.mark.parametrize("word", ["selam", "alem", "ayakkabı", "orijinal"])
def test_live_false_negative_is_a_valid_lemma(word):
    assert _word(word) == word


def test_turkish_case_folding_and_original_bank_remain():
    assert canonical_word("IŞIK") == "ışık"
    assert canonical_word("İŞİT") == "işit"
    assert {"masa", "anahtar", "radyo", "ışık", "kalem"} <= WORDS
    assert len(ORIGINAL_WORDS) == 792
    assert ORIGINAL_WORDS <= WORDS


def test_over_one_hundred_common_words_are_available_offline():
    assert len(set(COMMON_WORDS)) >= 100
    assert set(COMMON_WORDS) <= LEXICON


def test_common_proper_name_homographs_and_other_parts_of_speech():
    assert {"deniz", "ada", "bilge", "özgür", "gelmek", "bazı", "fakat", "merhaba"} <= LEXICON


def test_pinned_lexicon_matches_bundled_manifest():
    folder = Path(__file__).resolve().parents[2] / "assets" / "word_chain"
    snapshot = (folder / "lexicon.txt").read_bytes()
    manifest = json.loads((folder / "source.json").read_text(encoding="utf-8"))
    assert len(manifest["commit"]) == 40
    assert manifest["derived_sha256"] == hashlib.sha256(snapshot).hexdigest()
    assert manifest["derived_count"] == len(snapshot.decode("utf-8").splitlines())
    assert manifest["derived_count"] >= 20_000
    license_bytes = (folder / "LICENSE").read_bytes()
    assert b"Apache License" in license_bytes
    assert manifest["source_files"][0]["sha256"] == hashlib.sha256(license_bytes).hexdigest()


@pytest.mark.parametrize("word", [
    "Adidas", "adidas", "İstanbul", "ankara", "Yargıtay", "Abdülleziz",
    "ayakkabılarımızdan", "selamlarımızdan",
])
def test_proper_names_and_invented_inflections_are_not_lemmas(word):
    assert canonical_word(word) not in LEXICON
