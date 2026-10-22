"""Build color-preserving locked derivatives from the licensed card faces.

The upstream UNO originals remain byte-for-byte intact. ``grey`` registry keys
are retained for protocol compatibility, but now point to full-color locks.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw


def locked_face(face: Image.Image) -> Image.Image:
    original = face.convert("RGBA")
    width = height = 512
    image = Image.new("RGBA", (width, height))
    image.alpha_composite(original, ((width-original.width)//2, (height-original.height)//2))
    # Every source pixel remains intact. The badge lives in the transparent
    # right gutter, so even ten-pip cards and action symbols remain unobscured.
    scale = 3
    layer = Image.new("RGBA", (width * scale, height * scale))
    draw = ImageDraw.Draw(layer)
    left, top, right, bottom = image.getchannel("A").getbbox() or (0, 0, width, height)
    size = min(64, width-right-10)
    assert size >= 42, "card_requires_right_gutter"
    x, y = right + 5, (height-size)//2
    def box(coords):
        return tuple(round(v * scale) for v in coords)
    draw.rounded_rectangle(box((x, y, x + size, y + size)), radius=round(size*.22*scale), fill="#172334", outline="white", width=2*scale)
    draw.arc(box((x+size*.29, y+size*.16, x+size*.71, y+size*.69)), 180, 360, fill="white", width=max(3,round(size*.08))*scale)
    draw.rounded_rectangle(box((x+size*.22, y+size*.43, x+size*.78, y+size*.81)), radius=round(size*.06*scale), fill="white")
    draw.ellipse(box((x+size*.46, y+size*.53, x+size*.54, y+size*.62)), fill="#172334")
    draw.rectangle(box((x+size*.48, y+size*.59, x+size*.52, y+size*.70)), fill="#172334")
    layer = layer.resize(image.size, Image.Resampling.LANCZOS)
    image.alpha_composite(layer)
    return image


def build(root: Path) -> None:
    packs = (("playing_cards", "stickers", "grey"), ("uno_maildian", "png", "locked"))
    for name, faces, destination in packs:
        pack = root / "assets" / name
        target = pack / destination
        target.mkdir(exist_ok=True)
        paths = sorted((pack / faces).glob("*.png"))
        if name == "playing_cards":
            paths = [p for p in paths if p.stem != "back"]
        else:
            paths = [p for p in paths if (pack / "png_lowsat" / p.name).is_file()]
        hashes = {}
        for path in paths:
            with Image.open(path) as image:
                locked_face(image).save(target / path.name, optimize=True)
            relative = f"{destination}/{path.name}"
            hashes[relative] = hashlib.sha256((target/path.name).read_bytes()).hexdigest()
        (pack / "LOCKED.json").write_text(json.dumps({"design": "full-color-gutter-lock-v2", "count": len(paths), "sha256": hashes}, indent=2)+"\n", encoding="utf-8")
        if name == "playing_cards":
            source_path = pack / "SOURCE.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["sha256"].update(hashes)
            source["locked_design"] = "Full original colors; lock badge marks unavailable cards."
            source["derivative"] = 'Official vector sheets rasterized in Edge at 5659x2156 (faces) and 2156x1617 (backs); crop each 145x195 source-unit cell from 150x200 grid, LANCZOS fit 381x512 on transparent 512x512 canvas; back from official 140x190 PNG by LANCZOS resize; unavailable faces retain every visible source pixel and add a lock in the transparent right gutter.'
            source_path.write_text(json.dumps(source, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    build(Path(__file__).resolve().parents[1])
