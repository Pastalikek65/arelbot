"""Generate a synthetic image with each exact HRMN model; no fallback or secrets."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from config import Config
from src.ai.providers.cloudflare_image import ImageGenerationRequest, ImageProviderError
from src.ai.providers.hrmn_image import HrmnImageProvider, HrmnImageHTTPError
from src.key_manager import KeyManager, ServiceType


async def check(names: list[str]) -> int:
    logging.disable(logging.CRITICAL)  # Signed download URLs must stay private.
    manager = KeyManager()
    if not manager.keys.get(ServiceType.HRMN):
        print(json.dumps({"status": "not_configured"}))
        return 2
    provider = HrmnImageProvider(manager)
    failed = False
    try:
        for name in names:
            model = Config.HRMN_IMAGE_MODELS[0 if name == "prism" else 1]
            start = time.monotonic()
            artifact = None
            result = {"model": model}
            try:
                artifact = Path(await provider.generate(ImageGenerationRequest(
                    "A blue ceramic teapot on a cream background, no text.", model=model)))
                with Image.open(artifact) as image:
                    result.update(status="generated", format=image.format,
                                  width=image.width, height=image.height,
                                  bytes=artifact.stat().st_size)
                    image.verify()
            except ImageProviderError as error:
                failed = True
                result.update(status="failed", error=error.code)
                if isinstance(error, HrmnImageHTTPError):
                    result.update(http_status=error.http_status, request_id=error.request_id,
                                  upstream_reason=error.upstream_reason)
            finally:
                if artifact is not None:
                    artifact.unlink(missing_ok=True)
            result["seconds"] = round(time.monotonic() - start, 2)
            print(json.dumps(result), flush=True)
    finally:
        await provider.close()
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", choices=["prism", "flare"],
                        default=["prism", "flare"])
    args = parser.parse_args()
    raise SystemExit(asyncio.run(check(list(dict.fromkeys(args.models)))))
