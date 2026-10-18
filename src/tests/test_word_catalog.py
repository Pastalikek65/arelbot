from src.games.word_catalog import (
    DEFAULT_WORDS,
    WordCatalog,
    WordEntry,
    normalize_turkish,
    safe_word_entry,
)


def test_turkish_normalization_handles_diacritics_case_and_mojibake():
    assert normalize_turkish("\u00c4\u00b0stanbul") == "istanbul"
    assert normalize_turkish("\u015eA\u015eKIN") == "saskin"
    assert normalize_turkish("K\u0131rm\u0131z\u0131") == "kirmizi"
    assert normalize_turkish("\u00c4\u00b0stanbul") == "istanbul"


def test_default_catalog_is_broad_and_has_curated_metadata():
    catalog = WordCatalog()
    assert len(DEFAULT_WORDS) >= 80
    assert len(catalog.categories()) >= 8
    assert all(entry.description for entry in catalog.entries())
    assert all(len(entry.hints) >= 5 for entry in catalog.entries())
    assert all(entry.difficulty in {"kolay", "orta", "zor", "uzman"} for entry in catalog.entries())


def test_hints_never_echo_the_answer_and_are_progressive():
    catalog = WordCatalog()
    for entry in catalog.entries():
        normalized_answer = normalize_turkish(entry.word)
        hints = catalog.hints_for(entry)
        assert len(hints) >= 5
        assert all(normalized_answer not in normalize_turkish(hint) for hint in hints)
        assert hints == tuple(dict.fromkeys(hints))


def test_draw_is_seeded_and_deduplicates_per_scope_until_pool_exhausted():
    first = WordCatalog(seed=42)
    second = WordCatalog(seed=42)
    a = [first.draw(scope="chat:1", category="bilim", difficulty="orta").entry.key for _ in range(5)]
    b = [second.draw(scope="chat:1", category="bilim", difficulty="orta").entry.key for _ in range(5)]
    assert a == b
    assert len(a) == len(set(a))
    assert first.draw(scope="chat:1", category="bilim", difficulty="orta").cycle_reset is True


def test_filtering_rejects_unsafe_or_leaking_entries():
    unsafe = WordEntry(
        key="unsafe", word="siktir", category="günlük", difficulty="kolay",
        description="sakıncalı", hints=("kısa",),
    )
    leak = WordEntry(
        key="leak", word="elma", category="yemek", difficulty="kolay",
        description="kırmızı elma", hints=("meyve",),
    )
    assert safe_word_entry(unsafe) is False
    assert safe_word_entry(leak) is False


def test_category_and_alias_search_are_diacritic_insensitive():
    catalog = WordCatalog()
    assert catalog.by_category("COGRAFYA")
    results = catalog.search("İSTANBUL")
    assert any(normalize_turkish(entry.word) == "istanbul" for entry in results)


def test_entry_round_trip_is_stable():
    catalog = WordCatalog()
    entry = catalog.entries()[0]
    payload = entry.to_dict()
    restored = WordEntry.from_dict(payload)
    assert restored == entry
