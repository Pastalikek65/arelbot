"""Download a verified, reusable portrait database before a release.

python scripts/import_age_catalog.py --target 200
No generated images and no guesswork about a person's age at photo time.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
from pathlib import Path
import random
import sys
from urllib.parse import unquote

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.features.providers.age_photos import WikimediaAgePhotos, photo_row

CATEGORIES = ["Turkish male film actors", "Turkish film actresses", "American film actresses",
              "American male film actors", "English film actresses", "English male film actors",
              "American women singers", "American male pop singers", "British women singers",
              "Turkish pop singers", "French film actresses", "French male film actors",
              "American television actresses", "American male television actors",
              "American women politicians", "American male tennis players", "American female tennis players",
              "American male comedians", "American women comedians", "American male film directors",
              "Indian film actresses", "Canadian film actresses", "Canadian male film actors",
              "German film actresses", "American female models", "Australian film actresses"]


def write_attributions(payload, output):
    lines = ["# Wikimedia fotoğraf veritabanı · Kaynaklar", "", "Fotoğraflar yeniden üretilmemiştir. Her dosya kendi Creative Commons lisansına tabidir.", "",
             "| Kişi | Doğum günü | Çekim günü | Yaş | Fotoğraf ve lisans |", "| --- | --- | --- | ---: | --- |"]
    for row in payload['photos']:
        name = str(row['person_name']).replace('|', '\\|').replace('\n', ' ')
        credit = str(row['credit']).replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {name} | {row['birth_date']} | {row['photo_date']} | {row['target_age']} | [Commons]({row['source_url']}) · {credit} · [Lisans]({row['license_url']}) |")
    (output / 'ATTRIBUTION.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    logging.getLogger('urllib3').setLevel(logging.ERROR)
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=200)
    parser.add_argument("--output", type=Path, default=ROOT / "assets/guess_age")
    parser.add_argument("--revalidate", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cache = args.output / ".import-cache"
    cache.mkdir(exist_ok=True)
    source = WikimediaAgePhotos()
    catalog = args.output / "catalog.json"
    payload = json.loads(catalog.read_text(encoding="utf-8")) if catalog.is_file() else {"schema_version": 1, "photos": []}
    photos = {row["person_id"]: row for row in payload["photos"] if (args.output / row["filename"]).is_file()}
    if args.revalidate:
        previous = list(photos.values())
        photos = {}
        rejected = cache / "rejected"
        rejected.mkdir(exist_ok=True)
        for offset in range(0, len(previous), 40):
            batch = previous[offset:offset + 40]
            titles = {unquote(row['source_url'].split('/wiki/', 1)[1]).replace('_', ' '): row for row in batch}
            pages = source.image_info(list(titles))
            for page in pages:
                old = titles.get(page['title'])
                if not old:
                    continue
                person = {"person": {"value": "http://www.wikidata.org/entity/" + old['person_id']},
                          "personLabel": {"value": old['person_name']}, "birth": {"value": old['birth_date']}}
                row = photo_row(person, page)
                if row:
                    row.pop('download_url', None)
                    row.update(filename=old['filename'], sha256=old['sha256'])
                    photos[row['person_id']] = row
                else:
                    path = args.output / old['filename']
                    if path.parent.resolve() == args.output.resolve() and path.is_file():
                        path.replace(rejected / path.name)
        payload['photos'] = list(photos.values())
        catalog.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'stage':'revalidate','accepted':len(photos),'rejected':len(previous)-len(photos)}),flush=True)
    ids = set()
    for category in CATEGORIES:
        path = cache / (hashlib.sha256(category.encode()).hexdigest()[:16] + ".json")
        try:
            rows = json.loads(path.read_text()) if path.exists() else source.people(category)
            path.write_text(json.dumps(rows))
            ids.update(rows)
        except Exception as error:
            print(json.dumps({"stage": "category", "error": type(error).__name__}), flush=True)
    pending = sorted(ids - photos.keys())
    random.Random(20261018).shuffle(pending)
    print(json.dumps({"stage": "candidates", "count": len(pending), "downloaded": len(photos)}), flush=True)
    skipped, failures = 0, 0
    for offset in range(0, len(pending), 50):
        if len(photos) >= args.target:
            break
        batch = pending[offset:offset + 50]
        path = cache / (hashlib.sha256('|'.join(batch).encode()).hexdigest()[:16] + ".json")
        try:
            people = json.loads(path.read_text()) if path.exists() else source.portraits(batch)
            path.write_text(json.dumps(people))
            by_title = {}
            for person in people:
                title = "File:" + unquote(person["image"]["value"].rsplit('/', 1)[-1]).replace('_', ' ')
                by_title.setdefault(title, person)
            pages = source.image_info(list(by_title)) if by_title else []
            for page in pages:
                person = by_title.get(page["title"])
                row = photo_row(person, page) if person else None
                if not row or row["person_id"] in photos:
                    skipped += 1
                    continue
                try:
                    content = source.get(row.pop("download_url"), binary=True)
                    with Image.open(io.BytesIO(content)) as picture:
                        picture.verify()
                    suffix = ".jpg" if content.startswith(b"\xff\xd8") else ".png"
                    filename = row["person_id"].lower() + suffix
                    destination = args.output / filename
                    destination.write_bytes(content)
                    row.update(filename=filename, sha256=hashlib.sha256(content).hexdigest())
                    photos[row["person_id"]] = row
                    payload.update(photos=list(photos.values()), fetched_at=datetime.now(timezone.utc).isoformat(),
                                   source="Wikidata P569 + P18; Commons DateTimeOriginal and file license metadata")
                    temporary = catalog.with_suffix('.json.tmp')
                    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    temporary.replace(catalog)
                except Exception:
                    failures += 1
                if len(photos) >= args.target:
                    break
            print(json.dumps({"stage": "download", "processed": min(offset + 50, len(pending)), "downloaded": len(photos), "skipped": skipped, "failures": failures}), flush=True)
        except Exception as error:
            failures += 1
            print(json.dumps({"stage": "batch", "error": type(error).__name__, "downloaded": len(photos)}), flush=True)
    source.session.close()
    write_attributions(payload, args.output)
    print(json.dumps({"complete": len(photos) >= args.target, "photos": len(photos), "failures": failures}), flush=True)
    return 0 if len(photos) >= args.target else 1


if __name__ == "__main__":
    raise SystemExit(main())
