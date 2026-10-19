"""Bounded Wikimedia catalog acquisition, used by the offline import command.

The game never downloads unverified pictures from a Telegram update. Source
metadata and small Commons thumbnails are collected before a release.
"""

import asyncio
from datetime import date
import html
import re
from urllib.parse import unquote, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.features.contracts import ProviderHealth, ProviderRequest, ProviderResponse
from src.games.age_catalog import age_at


class WikimediaAgePhotos:
    name = "wikimedia_age_photos"
    capabilities = {"portrait_catalog"}
    ALLOWED_HOSTS = {"en.wikipedia.org", "www.wikidata.org", "query.wikidata.org", "commons.wikimedia.org", "upload.wikimedia.org", "thumb.wikimedia.org"}

    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "ArelbotAgeCatalog/1.0 (https://github.com/Pastalikek65/arelbot)", "Accept": "application/json"})
        if session is None:
            self.session.mount("https://", HTTPAdapter(max_retries=Retry(total=2, backoff_factor=1,
                               status_forcelist=[429, 500, 502, 503, 504], respect_retry_after_header=True)))

    def get(self, url, *, params=None, binary=False):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in self.ALLOWED_HOSTS or parsed.username or parsed.password:
            raise ValueError("AGE_CATALOG_SOURCE_INVALID")
        with self.session.get(url, params=params, timeout=(8, 35), allow_redirects=False, stream=True) as response:
            response.raise_for_status()
            if response.is_redirect:
                raise ValueError("AGE_CATALOG_REDIRECT_REJECTED")
            limit = 3_000_000 if binary else 8_000_000
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > limit:
                    raise ValueError("AGE_CATALOG_RESPONSE_TOO_LARGE")
                chunks.append(chunk)
            content = b"".join(chunks)
            if binary:
                if not content.startswith((b"\xff\xd8", b"\x89PNG\r\n\x1a\n")):
                    raise ValueError("AGE_CATALOG_IMAGE_INVALID")
                return content
            import json
            payload = json.loads(content)
            if isinstance(payload, dict) and payload.get("error"):
                raise ValueError("AGE_CATALOG_API_ERROR")
            return payload

    def people(self, category, *, limit=500):
        payload = self.get("https://en.wikipedia.org/w/api.php", params={"action": "query", "generator": "categorymembers",
            "gcmtitle": f"Category:{category}", "gcmtype": "page", "gcmlimit": min(500, limit),
            "prop": "pageprops", "ppprop": "wikibase_item", "format": "json", "formatversion": 2})
        return [p["pageprops"]["wikibase_item"] for p in payload.get("query", {}).get("pages", []) if p.get("pageprops", {}).get("wikibase_item")]

    def portraits(self, ids):
        if not ids or any(not re.fullmatch(r"Q\d+", value) for value in ids):
            return []
        query = ('SELECT ?person ?personLabel ?birth ?image WHERE { VALUES ?person {' +
                 ' '.join('wd:' + value for value in ids) +
                 '} ?person p:P569/psv:P569 ?node; wdt:P18 ?image. '
                 '?node wikibase:timeValue ?birth; wikibase:timePrecision ?precision. FILTER(?precision>=11) '
                 'SERVICE wikibase:label {bd:serviceParam wikibase:language "tr,en,mul,fr,de,es,it".} }')
        payload = self.get("https://query.wikidata.org/sparql", params={"query": query, "format": "json"})
        return payload.get("results", {}).get("bindings", [])

    def image_info(self, titles):
        return self.get("https://commons.wikimedia.org/w/api.php", params={"action": "query", "prop": "imageinfo",
            "titles": '|'.join(titles), "iiprop": "url|size|extmetadata|metadata|mime", "iiurlwidth": 500,
            "iiextmetadatafilter": "DateTimeOriginal|Artist|LicenseShortName|LicenseUrl|AttributionRequired",
            "format": "json", "formatversion": 2}).get("query", {}).get("pages", [])

    async def fetch(self, request: ProviderRequest):
        if request.operation != "portrait_catalog":
            raise ValueError("AGE_CATALOG_OPERATION_INVALID")
        data = await asyncio.wait_for(asyncio.to_thread(self.portraits, list(request.params.get("ids", []))[:100]), timeout=max(1, min(40, request.timeout_seconds)))
        return ProviderResponse(data=data, source="Wikidata / Wikimedia Commons", source_url="https://www.wikidata.org/")

    async def normalize(self, raw):
        return raw if isinstance(raw, list) else []

    async def health_check(self):
        try:
            pages = await asyncio.to_thread(self.image_info, ["File:Cate Blanchett by Gage Skidmore.jpg"])
            return ProviderHealth(self.name, bool(pages and pages[0].get("imageinfo")))
        except (requests.RequestException, ValueError):
            return ProviderHealth(self.name, False, detail="AGE_CATALOG_UNAVAILABLE")


def plain(value):
    return html.unescape(re.sub(r"<[^>]*>", "", str(value or ""))).strip()


def photo_row(person, info):
    """Reject uncertain dates, group/landscape pictures and unlicensed files."""
    try:
        image = info["imageinfo"][0]
        meta = image.get("extmetadata", {})
        taken = plain(meta.get("DateTimeOriginal", {}).get("value", ""))
        match = re.fullmatch(r"(\d{4})[-:](\d{2})[-:](\d{2})(?:[ T].*)?", taken)
        if not match:
            return None
        photographed = '-'.join(match.groups())
        capture = next((str(entry.get("value", "")) for entry in image.get("metadata", []) if entry.get("name") == "DateTimeOriginal"), "")
        camera_date = re.fullmatch(r"(\d{4}):(\d{2}):(\d{2}) \d{2}:\d{2}:\d{2}", capture)
        if not camera_date or '-'.join(camera_date.groups()) != photographed:
            return None
        filename_years = re.findall(r"(?:19|20)\d{2}", unquote(image.get("descriptionurl", "")))
        if filename_years and photographed[:4] not in filename_years:
            return None
        birth = person["birth"]["value"][:10]
        age = age_at(birth, photographed)
        if not 18 <= age <= 100 or date.fromisoformat(photographed) > date.today():
            return None
        width, height = int(image["width"]), int(image["height"])
        if min(width, height) < 300 or not .45 <= width / height <= 1.05:
            return None
        license_name = plain(meta.get("LicenseShortName", {}).get("value", ""))
        license_url = str(meta.get("LicenseUrl", {}).get("value", ""))
        if license_url.startswith("//"):
            license_url = "https:" + license_url
        if not license_url.startswith("https://creativecommons.org/"):
            return None
        if not (license_name.startswith(("CC BY", "CC0")) or license_name == "Public domain") or "NC" in license_name or "ND" in license_name:
            return None
        qid = person["person"]["value"].rsplit('/', 1)[-1]
        if person["personLabel"]["value"] == qid:
            return None
        artist = plain(meta.get("Artist", {}).get("value", ""))[:220]
        if not artist or not re.fullmatch(r"Q\d+", qid):
            return None
        return {"key": f"wikimedia_{qid}", "person_id": qid, "person_name": person["personLabel"]["value"],
                "birth_date": birth, "photo_date": photographed, "target_age": age,
                "source_url": image["descriptionurl"], "credit": f"{artist} · {license_name}",
                "license_url": license_url, "capture_verified": "matching_camera_exif_and_commons_date",
                "download_url": image.get("thumburl", image["url"])}
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
        return None
