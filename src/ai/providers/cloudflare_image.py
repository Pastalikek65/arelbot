"""Typed Cloudflare Workers AI image provider.

The provider owns external transport, key rotation, response validation and
temporary-file creation. Credentials and upstream response text never cross
this boundary.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import tempfile
from dataclasses import dataclass
from typing import Any

import httpx

from config import Config
from src.key_manager import KeyManager, NoKeysConfiguredError, ServiceCircuitOpenError, ServiceType
from src.security.tls import httpx_verify_kwargs

logger = logging.getLogger(__name__)


class ImageProviderError(RuntimeError):
    """Stable, user-safe image provider error."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    width: int = 1024
    height: int = 1024
    num_steps: int = 4
    negative_prompt: str | None = None
    model: str | None = None
    max_output_bytes: int | None = None
    timeout_seconds: float = 120.0


class CloudflareImageProvider:
    """Cloudflare Flux adapter with key rotation and bounded artifacts."""

    name = "cloudflare_image"
    capabilities = {"image_generation", "untrusted_provider_output"}
    source_name = "Cloudflare Workers AI"
    source_url = "https://developers.cloudflare.com/workers-ai/"
    timeout_seconds = 120.0
    max_prompt_chars = 4000
    max_dimension = 2048
    max_steps = 20

    def __init__(
        self,
        key_manager: KeyManager,
        *,
        client: httpx.AsyncClient | None = None,
        endpoint_base: str = "https://api.cloudflare.com/client/v4/accounts",
        max_attempts: int = 5,
    ) -> None:
        if not endpoint_base.startswith("https://"):
            raise ValueError("cloudflare_image_endpoint_must_be_https")
        self.key_manager = key_manager
        self.endpoint_base = endpoint_base.rstrip("/")
        self.max_attempts = max(1, min(int(max_attempts), 10))
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds, connect=10.0),
                headers={"Accept": "application/json, image/*", "User-Agent": "Arelbot/1.0"},
                follow_redirects=False,
                **httpx_verify_kwargs(),
            )
        return self._owned_client

    @classmethod
    def _validate_request(cls, request: ImageGenerationRequest) -> None:
        prompt = str(request.prompt or "").strip()
        if not prompt:
            raise ImageProviderError("image_prompt_invalid")
        if len(prompt) > cls.max_prompt_chars:
            raise ImageProviderError("image_prompt_too_long")
        for name in ("width", "height"):
            value = int(getattr(request, name))
            if value < 64 or value > cls.max_dimension:
                raise ImageProviderError("image_dimensions_invalid")
        if int(request.num_steps) < 1 or int(request.num_steps) > cls.max_steps:
            raise ImageProviderError("image_steps_invalid")
        if request.max_output_bytes is not None and int(request.max_output_bytes) < 1024:
            raise ImageProviderError("image_output_limit_invalid")

    @staticmethod
    def _parse_key(raw_key: str) -> tuple[str, str]:
        account_id, separator, api_token = str(raw_key or "").partition(":")
        if not separator or not account_id.strip() or not api_token.strip():
            raise ImageProviderError("image_provider_not_configured")
        return account_id.strip(), api_token.strip()

    @staticmethod
    def _is_policy_rejection(value: object) -> bool:
        try:
            text = json.dumps(value, ensure_ascii=False).casefold()
        except (TypeError, ValueError):
            text = type(value).__name__.casefold()
        return "nsfw" in text or ("safety" in text and "reject" in text)

    @classmethod
    def _decode_payload(cls, response: httpx.Response) -> bytes:
        content_type = response.headers.get("content-type", "").casefold()
        if content_type.startswith("image/"):
            payload = bytes(response.content)
        else:
            try:
                body = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise ImageProviderError("image_provider_invalid_json", retryable=True) from exc
            if not isinstance(body, dict):
                raise ImageProviderError("image_provider_schema_error", retryable=True)
            if not body.get("success"):
                if cls._is_policy_rejection(body):
                    raise ImageProviderError("image_policy_rejected")
                raise ImageProviderError("image_provider_rejected")
            result = body.get("result")
            encoded = result.get("image") if isinstance(result, dict) else None
            if not isinstance(encoded, str) or not encoded:
                raise ImageProviderError("image_provider_schema_error", retryable=True)
            try:
                payload = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ImageProviderError("image_provider_invalid_payload", retryable=True) from exc
        if not payload:
            raise ImageProviderError("image_provider_empty_payload", retryable=True)
        return payload

    @staticmethod
    def _write_artifact(payload: bytes, max_output_bytes: int | None) -> str:
        if max_output_bytes is not None and len(payload) > int(max_output_bytes):
            raise ImageProviderError("image_output_too_large")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png", prefix="arel-cf-") as handle:
            handle.write(payload)
            return handle.name

    async def generate(self, request: ImageGenerationRequest) -> str:
        self._validate_request(request)
        client = await self._get_client()
        params: dict[str, Any] = {
            "prompt": request.prompt.strip(),
            "width": int(request.width),
            "height": int(request.height),
            "num_steps": int(request.num_steps),
        }
        if request.negative_prompt:
            params["negative_prompt"] = request.negative_prompt[: self.max_prompt_chars]

        last_error: ImageProviderError | None = None
        for _ in range(self.max_attempts):
            raw_key: str | None = None
            try:
                raw_key = self.key_manager.get_key(ServiceType.CLOUDFLARE)
                account_id, api_token = self._parse_key(raw_key)
            except (NoKeysConfiguredError, ServiceCircuitOpenError):
                raise ImageProviderError("image_provider_not_configured")
            endpoint = f"{self.endpoint_base}/{account_id}/ai/run/{request.model or Config.CLOUDFLARE_IMAGE_MODEL}"
            try:
                response = await client.post(
                    endpoint,
                    json=params,
                    headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
                    timeout=min(max(float(request.timeout_seconds), 0.5), self.timeout_seconds),
                )
                status = int(response.status_code)
                if status == 200:
                    payload = self._decode_payload(response)
                    artifact = self._write_artifact(payload, request.max_output_bytes)
                    self.key_manager.report_success(ServiceType.CLOUDFLARE, raw_key)
                    return artifact
                if self._is_policy_rejection(response.text[:512]):
                    raise ImageProviderError("image_policy_rejected")
                if status in {401, 403, 429}:
                    last_error = ImageProviderError(
                        "image_provider_quota" if status == 429 else "image_provider_auth",
                        retryable=True,
                    )
                    self.key_manager.report_error(ServiceType.CLOUDFLARE, raw_key, str(status))
                    continue
                last_error = ImageProviderError(
                    "image_provider_upstream" if status >= 500 else "image_provider_rejected",
                    retryable=status >= 500,
                )
                self.key_manager.report_error(ServiceType.CLOUDFLARE, raw_key, f"status_{status}")
            except ImageProviderError as exc:
                if not exc.retryable:
                    raise
                last_error = exc
                self.key_manager.report_error(ServiceType.CLOUDFLARE, raw_key, exc.code)
            except (httpx.HTTPError, TimeoutError):
                last_error = ImageProviderError("image_provider_network", retryable=True)
                self.key_manager.report_error(ServiceType.CLOUDFLARE, raw_key, "network")

        raise last_error or ImageProviderError("image_provider_unavailable", retryable=True)

    async def health_check(self) -> dict[str, Any]:
        configured = bool(getattr(self.key_manager, "keys", {}).get(ServiceType.CLOUDFLARE))
        return {"name": self.name, "healthy": configured, "detail": "configured" if configured else "key_not_configured"}

    async def close(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None
