from src.games.word_catalog_adapter import WordCatalogAdapter
from src.games.word_guess import WordGuessGame


def test_adapter_normalizes_category_and_difficulty_and_preserves_hint_safety():
    adapter = WordCatalogAdapter(
        [
            {
                "word": "KARANFİL",
                "category": "şehirler",
                "difficulty": "kolay",
                "description": "Güzel kokulu bir çiçek türüdür.",
                "hints": ["Çiçekçilerde sık görülür."],
            },
            {
                "word": "KİTAP",
                "category": "genel kültür",
                "description": "Okunabilen sayfalı bir eserdir.",
            },
        ],
        seed=7,
    )

    draw = adapter.draw(scope="chat:1", category="ŞEHİRLER", difficulty="kolay")

    assert draw.entry.word == "KARANFİL"
    assert draw.entry.category == "sehirler"
    assert draw.entry.difficulty == "kolay"
    assert all("karanfil" not in clue.casefold() for clue in adapter.hints(draw.entry))


def test_adapter_is_seeded_and_has_scope_repeat_guard():
    records = [
        {"word": "ALFA", "category": "genel", "description": "İlk örnek kavramdır."},
        {"word": "BETA", "category": "genel", "description": "İkinci örnek kavramdır."},
        {"word": "GAMA", "category": "genel", "description": "Üçüncü örnek kavramdır."},
    ]

    first = WordCatalogAdapter(records, seed=42)
    second = WordCatalogAdapter(records, seed=42)
    a = [first.draw(scope="chat:1", category="genel").entry.key for _ in range(3)]
    b = [second.draw(scope="chat:1", category="genel").entry.key for _ in range(3)]

    assert a == b
    assert len(set(a)) == 3
    assert first.draw(scope="chat:1", category="genel").cycle_reset is True
    # A different scope gets an independent repeat guard.
    assert first.draw(scope="chat:2", category="genel").entry.key in set(a)


def test_word_guess_uses_catalog_projection_without_changing_legacy_state_shape():
    game = WordGuessGame(seed=11)

    result = game.start(123, category="bilim", difficulty="zor")
    state = result["game_state"]

    assert state["category"] == "bilim"
    assert state["difficulty"] == "hard"
    assert state["catalog_key"]
    assert state["catalog_scope"] == "chat:123"
    assert state["hints"]
    assert all(state["word"].casefold() not in hint.casefold() for hint in state["hints"])


def test_word_guess_can_restore_old_state_and_reveal_next_hint():
    game = WordGuessGame(seed=3)
    old_state = {
        "word": "KEDİ",
        "description": "Evlerde yaşayan sevimli bir hayvandır.",
        "hints": ["Dört harflidir."],
        "hint_index": 0,
        "attempts": 0,
    }

    assert game.get_next_hint(old_state) == "Dört harflidir."
    assert old_state["hint_index"] == 1
