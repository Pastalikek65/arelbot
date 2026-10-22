"""Corresponding source is exact, chunked, and cannot read arbitrary files."""
import gzip
import json
from pathlib import Path

import pytest

from scripts.deploy_release import build_source_offer
from src.services.uno_source_offer import UnoSourceOffer


@pytest.mark.asyncio
async def test_source_parts_reassemble_exact_git_archive_and_detect_tampering(tmp_path):
    archive = tmp_path / "git-source.tar"
    original = bytes(range(256)) * 50
    archive.write_bytes(original)
    release = tmp_path / "release"
    release.mkdir()
    build_source_offer(archive, release, "f" * 40, chunk_bytes=100)
    offer = UnoSourceOffer(release)
    parts = [data async for _, data in offer.parts()]
    assert len(parts) > 1
    assert gzip.decompress(b"".join(parts)) == original
    first = next((release / "source").iterdir())
    first.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="UNO_SOURCE_UNAVAILABLE"):
        _ = [data async for _, data in offer.parts()]


@pytest.mark.asyncio
async def test_source_manifest_rejects_paths_outside_source_bundle(tmp_path):
    (tmp_path / "SOURCE_OFFER.json").write_text(json.dumps({
        "commit": "f" * 40, "parts": [{"file": "../private.env"}],
    }))
    with pytest.raises(ValueError, match="UNO_SOURCE_UNAVAILABLE"):
        _ = [part async for part in UnoSourceOffer(tmp_path).parts()]
