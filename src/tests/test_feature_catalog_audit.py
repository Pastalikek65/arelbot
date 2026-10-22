from __future__ import annotations

from types import SimpleNamespace

from scripts.feature_catalog_audit import audit_feature_catalog, documented_feature_keys


def test_catalog_audit_requires_every_runtime_feature_once():
    definitions = [SimpleNamespace(key="weather"), SimpleNamespace(key="crypto")]
    markdown = (
        "| Key | Komutlar |\n"
        "|---|---|\n"
        "| `weather` | `/hava` |\n"
        "| `weather` | duplicate |\n"
    )

    report = audit_feature_catalog(definitions, markdown)

    assert report.ok is False
    assert report.missing == ("crypto",)
    assert report.duplicates == ("weather",)
    assert report.undocumented == ()


def test_catalog_audit_accepts_exact_runtime_documentation_parity():
    markdown = "| `weather` | `/hava` |\n| `crypto` | `/kripto` |\n"

    report = audit_feature_catalog(
        [SimpleNamespace(key="weather"), SimpleNamespace(key="crypto")],
        markdown,
    )

    assert report.ok is True
    assert documented_feature_keys(markdown) == ("weather", "crypto")
    assert report.as_dict() == {
        "ok": True,
        "runtime_count": 2,
        "documented_count": 2,
        "missing": [],
        "undocumented": [],
        "duplicates": [],
    }


def test_numeric_game_key_is_a_real_catalog_entry():
    assert audit_feature_catalog([SimpleNamespace(key='2048')], '| `2048` | `/2048` |\n').ok
