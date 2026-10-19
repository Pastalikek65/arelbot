import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from src.features.providers.age_photos import WikimediaAgePhotos


class Response:
    is_redirect = False
    def __init__(self, content, status=200):
        self.content, self.status = content, status
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(response=SimpleNamespace(status_code=self.status))
    def iter_content(self, size):
        yield self.content


def provider(response):
    session = SimpleNamespace(headers={}, get=Mock(return_value=response))
    return WikimediaAgePhotos(session), session


def test_current_wikimedia_thumbnail_host_is_supported_without_arbitrary_hosts():
    source, session = provider(Response(b"\xff\xd8photo"))
    assert source.get("https://thumb.wikimedia.org/wikipedia/commons/thumb/photo.jpg", binary=True).startswith(b"\xff\xd8")
    with pytest.raises(ValueError, match="SOURCE_INVALID"):
        source.get("https://thumb.wikimedia.org.evil.invalid/photo.jpg", binary=True)
    assert session.get.call_count == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_upstream_failure_is_not_cached_as_a_catalog(status):
    source, _ = provider(Response(b'{}', status))
    with pytest.raises(requests.HTTPError):
        source.people("Actors")


def test_timeout_and_malformed_json_fail_explicitly():
    source, session = provider(Response(b'not-json'))
    with pytest.raises(ValueError):
        source.people("Actors")
    session.get.side_effect = requests.Timeout()
    with pytest.raises(requests.Timeout):
        source.people("Actors")


def test_redirect_and_non_image_responses_are_rejected():
    response = Response(b'<html>not an image</html>')
    source, _ = provider(response)
    with pytest.raises(ValueError, match="IMAGE_INVALID"):
        source.get("https://upload.wikimedia.org/file.jpg", binary=True)
    response.is_redirect = True
    with pytest.raises(ValueError, match="REDIRECT_REJECTED"):
        source.get("https://upload.wikimedia.org/file.jpg", binary=True)
