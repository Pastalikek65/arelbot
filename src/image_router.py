"""Backward-compatible image generation facade.

Telegram handlers and workers keep using ``ImageRouter`` while all external
Cloudflare transport lives in the typed AI provider adapter.
"""

from __future__ import annotations

import re

from config import Config
from src.ai.providers.cloudflare_image import CloudflareImageProvider, ImageGenerationRequest
from src.key_manager import KeyManager


class ImageRouter:
    def __init__(self, key_manager: KeyManager, provider: CloudflareImageProvider | None = None):
        self.provider = provider or CloudflareImageProvider(key_manager)

    def _parse_params(self, prompt: str) -> tuple[str, dict[str, object]]:
        params: dict[str, object] = {"width": 1024, "height": 1024, "num_steps": 4}
        neg_match = re.search(r"--negative\s+(.*?)(?=\s--|$)", prompt)
        if neg_match:
            params["negative_prompt"] = neg_match.group(1).strip()
            prompt = prompt.replace(neg_match.group(0), "")
        for option, key in (("width", "width"), ("height", "height"), ("steps", "num_steps")):
            match = re.search(rf"--{option}\s+(\d+)", prompt)
            if match:
                value = int(match.group(1))
                params[key] = min(value, 20) if key == "num_steps" else value
                prompt = prompt.replace(match.group(0), "")
        return prompt.strip(), params

    async def generate_image(self, prompt: str, *, max_output_bytes: int | None = None) -> str:
        cleaned_prompt, params = self._parse_params(prompt)
        request = ImageGenerationRequest(
            prompt=cleaned_prompt,
            width=int(params["width"]),
            height=int(params["height"]),
            num_steps=int(params["num_steps"]),
            negative_prompt=params.get("negative_prompt"),
            model=Config.CLOUDFLARE_IMAGE_MODEL,
            max_output_bytes=max_output_bytes,
        )
        return await self.provider.generate(request)

    async def health_check(self) -> dict:
        return await self.provider.health_check()

    async def close(self) -> None:
        await self.provider.close()
