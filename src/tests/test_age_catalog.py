import hashlib
import json
from pathlib import Path

from src.games.guess_age import builtin_assets


def _row(tmp_path: Path, **overrides):
    photo = b"\xff\xd8catalog-photo"
    (tmp_path / "q42.jpg").write_bytes(photo)
    return {
        "key": "wikimedia_Q42", "person_id": "Q42", "filename": "q42.jpg",
        "person_name": "Örnek Kişi", "birth_date": "1970-04-15",
        "photo_date": "2020-04-14", "target_age": 49,
        "source_url": "https://commons.wikimedia.org/wiki/File:Portrait.jpg",
        "credit": "Fotoğrafçı · CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "sha256": hashlib.sha256(photo).hexdigest(), **overrides,
        "capture_verified": "matching_camera_exif_and_commons_date",
    }


def test_downloaded_catalog_is_used_instead_of_small_builtin_pool(tmp_path):
    row = _row(tmp_path)
    (tmp_path / "catalog.json").write_text(json.dumps({"schema_version": 1, "photos": [row]}), encoding="utf-8")
    assets = builtin_assets(str(tmp_path))
    assert len(assets) == 1 and assets[0].key == row["key"]
    assert assets[0].target_age == 49


def test_catalog_rejects_wrong_age_missing_sources_and_path_escape(tmp_path):
    rows = [_row(tmp_path), _row(tmp_path, key="bad_age", target_age=50),
            _row(tmp_path, key="escape", filename="../q42.jpg"),
            _row(tmp_path, key="bad_source", source_url="https://example.invalid/photo"),
            _row(tmp_path, key="bad_hash", sha256="0" * 64)]
    (tmp_path / "catalog.json").write_text(json.dumps({"schema_version": 1, "photos": rows}), encoding="utf-8")
    assert [a.key for a in builtin_assets(str(tmp_path))] == ["wikimedia_Q42"]


def test_import_rejects_derivative_creation_date_as_the_persons_photo_date():
    from src.features.providers.age_photos import photo_row
    person = {"person": {"value": "http://www.wikidata.org/entity/Q42"}, "personLabel": {"value": "Person"}, "birth": {"value": "1951-01-12T00:00:00Z"}}
    info = {"imageinfo": [{"width": 600, "height": 800, "url": "https://upload.wikimedia.org/portrait.jpg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Person1994-close.jpg",
        "extmetadata": {"DateTimeOriginal": {"value": "2009-03-30 18:08"},
                        "Artist": {"value": "Photographer; derivative work by Editor"},
                        "LicenseShortName": {"value": "CC BY 2.0"},
                        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/2.0/"}}}]}
    assert photo_row(person, info) is None


def test_import_requires_matching_camera_capture_and_source_dates():
    from src.features.providers.age_photos import photo_row
    person = {"person": {"value": "http://www.wikidata.org/entity/Q42"}, "personLabel": {"value": "Person"}, "birth": {"value": "1970-04-15T00:00:00Z"}}
    image = {"width": 600, "height": 800, "url": "https://upload.wikimedia.org/portrait.jpg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Person2020.jpg",
        "metadata": [{"name": "DateTimeOriginal", "value": "2020:04:14 12:00:00"}],
        "extmetadata": {"DateTimeOriginal": {"value": "2020-04-14 12:00:00"},
                        "Artist": {"value": "Photographer"}, "LicenseShortName": {"value": "CC BY 4.0"},
                        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"}}}
    assert photo_row(person, {"imageinfo": [image]})["target_age"] == 49
    image["metadata"][0]["value"] = "2019:04:14 12:00:00"
    assert photo_row(person, {"imageinfo": [image]}) is None
