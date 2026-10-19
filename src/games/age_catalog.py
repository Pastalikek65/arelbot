"""Validate a downloaded, attributed Wikimedia portrait catalog offline."""

from datetime import date
import hashlib
import json
from pathlib import Path
import re


def age_at(birth: str, photographed: str) -> int:
    born, taken = date.fromisoformat(birth), date.fromisoformat(photographed)
    return taken.year - born.year - ((taken.month, taken.day) < (born.month, born.day))


def load_age_catalog(root: Path):
    from src.games.guess_age import AgeAsset

    manifest = root / "catalog.json"
    if not manifest.is_file():
        return ()
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1 or not isinstance(payload.get("photos"), list):
            return ()
    except (OSError, ValueError, AttributeError):
        return ()
    assets, seen = [], set()
    for row in payload["photos"]:
        try:
            filename = str(row["filename"])
            if not re.fullmatch(r"[A-Za-z0-9_-]+\.(?:jpg|jpeg|png)", filename):
                continue
            path = root / filename
            key = str(row["key"])
            if key in seen or not path.is_file() or path.is_symlink():
                continue
            if not str(row["source_url"]).startswith("https://commons.wikimedia.org/wiki/File:"):
                continue
            if not str(row["license_url"]).startswith(("https://creativecommons.org/", "https://commons.wikimedia.org/")):
                continue
            calculated = age_at(str(row["birth_date"]), str(row["photo_date"]))
            if (not 18 <= calculated <= 100 or calculated != int(row["target_age"])
                    or date.fromisoformat(str(row["photo_date"])) > date.today()
                    or row.get("capture_verified") != "matching_camera_exif_and_commons_date"):
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() != str(row["sha256"]):
                continue
            if not row.get("person_name") or not row.get("credit"):
                continue
            assets.append(AgeAsset(key, filename, calculated, str(row["person_name"])[:120],
                                   str(row["photo_date"]), str(row["source_url"]), str(row["credit"])[:300],
                                   str(row["birth_date"]), str(row["license_url"]), str(row["sha256"]),
                                   str(row.get("person_id", key))))
            seen.add(key)
        except (KeyError, TypeError, ValueError, OSError):
            continue
    return tuple(assets)
