"""Backward-compatible image generation facade.

Telegram handlers and workers keep using ``ImageRouter`` while all external
Cloudflare transport lives in the typed AI provider adapter.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace

from config import Config
from src.ai.providers.cloudflare_image import CloudflareImageProvider, ImageGenerationRequest
from src.ai.providers.cloudflare_image import ImageProviderError
from src.ai.providers.hrmn_image import HrmnImageProvider
from src.key_manager import KeyManager, ServiceType


class ImageRouter:
    def __init__(self, key_manager: KeyManager, provider: CloudflareImageProvider | None = None):
        self.provider = provider or CloudflareImageProvider(key_manager)
        self.hrmn = (HrmnImageProvider(key_manager) if provider is None and
                     getattr(key_manager, "keys", {}).get(ServiceType.HRMN) else None)

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
        selected = re.search(r"(?:^|\s)--model\s+(\S+)", prompt)
        models = list(Config.HRMN_IMAGE_MODELS)
        if selected:
            requested = selected.group(1)
            aliases = {"prism": "gpt-image-2.5-prism", "flare": "gpt-image-2.5-flare"}
            requested = aliases.get(requested, requested)
            if requested not in (*models, *Config.HRMN_IMAGE_LEGACY_MODELS) or self.hrmn is None:
                raise ImageProviderError("image_model_invalid")
            models = [requested]
            prompt = prompt.replace(selected.group(0), " ")
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
        if self.hrmn is not None:
            if selected:
                return await self.hrmn.generate(replace(request, model=models[0]))
            # Five slow HRMN models must not exhaust the worker's entire job
            # deadline before the existing Cloudflare backup gets a chance.
            try:
                async with asyncio.timeout(max(0.01, Config.IMAGE_JOB_TIMEOUT_SECONDS * 0.6)):
                    for model in models:
                        try:
                            return await self.hrmn.generate(replace(request, model=model))
                        except ImageProviderError as error:
                            if error.code in {"image_prompt_invalid", "image_prompt_too_long",
                                              "image_dimensions_invalid", "image_steps_invalid",
                                              "image_output_limit_invalid", "image_policy_rejected"}:
                                raise
            except TimeoutError:
                pass
        return await self.provider.generate(request)

    async def health_check(self) -> dict:
        if self.hrmn is not None:
            health = await self.hrmn.health_check()
            if health.get("healthy"):
                return health
        return await self.provider.health_check()

    async def close(self) -> None:
        await self.provider.close()
        if self.hrmn is not None:
            await self.hrmn.close()
