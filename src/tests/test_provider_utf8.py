from pathlib import Path


def test_typed_provider_sources_do_not_contain_mojibake_or_replacement_text():
    root = Path(__file__).parents[1] / "features" / "providers"
    # Build markers from code points so this test is not reported as a
    # mojibake source by the architecture checker itself.
    forbidden = tuple(
        chr(code_point)
        for code_point in (0x00C3, 0x00C4, 0x00C5, 0x00C2, 0x00E2, 0xFFFD)
    )
    offenders = []
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in forbidden):
            offenders.append(path.name)
    assert offenders == [], f"provider source text contains mojibake: {offenders}"
