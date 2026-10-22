"""Bounded HRMN image generation; returned download URLs never receive auth."""
from __future__ import annotations

import asyncio
import base64
import binascii
import io
import re
import logging
import json
from contextvars import ContextVar
import time
from urllib.parse import urlsplit

import httpx
from PIL import Image

from config import Config
from src.ai.providers.cloudflare_image import CloudflareImageProvider, ImageGenerationRequest, ImageProviderError
from src.key_manager import KeyManagerError, ServiceType
from src.security.public_url import validate_public_url

_image_transport = ContextVar("hrmn_image_transport", default=False)


class _PrivateImageTransport(logging.Filter):
    def filter(self, record):
        return not _image_transport.get()


# Workers can enable HTTPX's INFO request logging. Suppress only this
# adapter's transport records, including signed image URLs, in its own task.
if not any(isinstance(f, _PrivateImageTransport) for f in logging.getLogger("httpx").filters):
    logging.getLogger("httpx").addFilter(_PrivateImageTransport())


class HrmnImageHTTPError(ImageProviderError):
    """Bounded, allowlisted diagnostics; never exposes upstream text or URLs."""

    def __init__(self, status: int, body: bytes):
        super().__init__("image_provider_quota" if status == 429 else "image_provider_upstream",
                         retryable=status == 429 or status >= 500)
        self.http_status = status
        self.request_id = None
        self.upstream_reason = None
        try:
            error = json.loads(body).get("error", {})
            candidate = error.get("request_id")
            if isinstance(candidate, str) and re.fullmatch(r"req_[A-Za-z0-9]{1,64}", candidate):
                self.request_id = candidate
            if error.get("message") == "no provider responded, server side error":
                self.upstream_reason = "no_provider_responded"
        except (ValueError, AttributeError, TypeError):
            pass


class HrmnImageProvider(CloudflareImageProvider):
    name = "hrmn_image"
    source_name = "HRMN"
    source_url = "https://api.hrmn.tr/v1"
    timeout_seconds = 35.0

    def __init__(self, key_manager, *, client=None):
        endpoint = Config.HRMN_URL
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or parsed.hostname != "api.hrmn.tr" or parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise ValueError("hrmn_endpoint_invalid")
        super().__init__(key_manager, client=client, endpoint_base=endpoint, max_attempts=1)
        self._cooldowns: dict[str, float] = {}

    async def _read(self, client, method, url, *, limit, **kwargs):
        token = _image_transport.set(True)
        try:
            async with client.stream(method, url, follow_redirects=False, **kwargs) as response:
                if response.status_code != 200:
                    diagnostic = bytearray()
                    async for chunk in response.aiter_bytes():
                        diagnostic.extend(chunk[:4096 - len(diagnostic)])
                        if len(diagnostic) >= 4096:
                            break
                    raise HrmnImageHTTPError(response.status_code, bytes(diagnostic))
                chunks, length = [], 0
                async for chunk in response.aiter_bytes():
                    length += len(chunk)
                    if length > limit:
                        raise ImageProviderError("image_output_too_large", retryable=True)
                    chunks.append(chunk)
                return b"".join(chunks)
        finally:
            _image_transport.reset(token)

    async def generate(self, request: ImageGenerationRequest) -> str:
        self._validate_request(request)
        model = request.model or Config.HRMN_IMAGE_MODELS[0]
        if model not in Config.HRMN_IMAGE_MODELS:
            raise ImageProviderError("image_model_invalid")
        if self._cooldowns.get(model, 0) > time.monotonic():
            raise ImageProviderError("image_provider_unavailable", retryable=True)
        try:
            return await asyncio.wait_for(self._generate(request, model), self.timeout_seconds)
        except (ImageProviderError, httpx.HTTPError, TimeoutError, KeyManagerError) as error:
            self._cooldowns[model] = time.monotonic() + 30
            if isinstance(error, ImageProviderError):
                raise
            raise ImageProviderError("image_provider_unavailable", retryable=True) from None

    async def _generate(self, request, model):
        key = self.key_manager.get_key(ServiceType.HRMN)
        client = await self._get_client()
        limit = min(request.max_output_bytes or Config.IMAGE_MAX_OUTPUT_BYTES, Config.IMAGE_MAX_OUTPUT_BYTES)
        prompt = request.prompt.strip()
        if request.negative_prompt:
            prompt += "\nAvoid: " + request.negative_prompt
        body = await self._read(client, "POST", self.endpoint_base + "/chat/completions",
            limit=limit * 2 + 65536,
            headers={"Authorization": "Bearer " + key},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "size": f"{request.width}x{request.height}", "stream": False},
            timeout=self.timeout_seconds)
        try:
            response = json.loads(body)
            if "choices" in response:
                content = response["choices"][0]["message"]["content"]
                if not isinstance(content, str):
                    raise ValueError("image_content_invalid")
                match = re.fullmatch(r"\s*!\[[^\]]*\]\((https://[^\s)]+)\)\s*", content)
                data = {"url": match.group(1) if match else content.strip()}
            else:
                data = response["data"][0]
            if data.get("b64_json"):
                payload = base64.b64decode(data["b64_json"], validate=True)
            else:
                url = await validate_public_url(data["url"])
                if urlsplit(url).scheme != "https":
                    raise ValueError("image_url_requires_https")
                payload = await self._read(client, "GET", url, limit=limit, timeout=self.timeout_seconds)
            if not payload or len(payload) > limit:
                raise ValueError("image_size_invalid")
            with Image.open(io.BytesIO(payload)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 16_777_216:
                    raise ValueError("image_format_invalid")
                image.verify()
        except (KeyError, IndexError, AttributeError, TypeError, ValueError, OSError,
                binascii.Error, Image.DecompressionBombError):
            raise ImageProviderError("image_provider_invalid_payload", retryable=True) from None
        return self._write_artifact(payload, limit)

    async def health_check(self):
        ready = bool(self.key_manager.keys.get(ServiceType.HRMN))
        return {"name": self.name, "healthy": ready, "detail": "configured" if ready else "key_not_configured"}
