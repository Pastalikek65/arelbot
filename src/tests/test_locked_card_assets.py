"""Unavailable cards retain all original pixels outside a compact lock badge."""
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageChops
import pytest

ROOT = Path(__file__).resolve().parents[2] / "assets"


@pytest.mark.parametrize("pack,faces,locked,count", [
    ("playing_cards", "stickers", "grey", 52),
    ("uno_maildian", "png", "locked", 54),
])
def test_locked_deck_preserves_color_ranks_silhouette_and_has_visible_badge(pack, faces, locked, count):
    root = ROOT / pack
    manifest = json.loads((root / "LOCKED.json").read_text())
    assert manifest["count"] == count
    assert len(manifest["sha256"]) == count
    expected = {p.name for p in (root / faces).glob("*.png") if (p.stem != "back" if pack == "playing_cards" else (root / "png_lowsat" / p.name).is_file())}
    assert set(manifest["sha256"]) == {f"{locked}/{name}" for name in expected}
    for relative, digest in manifest["sha256"].items():
        path = root / relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        with Image.open(root / faces / path.name) as original, Image.open(path) as result:
            original, result = original.convert("RGBA"), result.convert("RGBA")
            assert result.size == (512, 512)
            canvas = Image.new("RGBA", result.size)
            canvas.alpha_composite(original, ((512-original.width)//2, (512-original.height)//2))
            bounds=canvas.getchannel("A").getbbox()
            # Every visible face pixel, including all pips/artwork, is exact.
            mask = canvas.getchannel("A").point(lambda alpha: 255 if alpha else 0)
            assert Image.composite(result, canvas, mask).tobytes() == canvas.tobytes()
            changed = ImageChops.difference(canvas, result).getbbox()
            assert changed is not None
            assert changed[0] > bounds[2]
            assert path.stat().st_size <= 512*1024
