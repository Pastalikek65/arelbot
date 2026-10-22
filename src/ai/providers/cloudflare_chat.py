"""Workers AI text adapter; account identifiers never become bearer tokens."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from src.ai.openai_compatible import OpenAICompatibleProvider, AIProviderConfigurationError, AIProviderProtocolError
from src.ai.provider_base import CompletionRequest, CompletionResponse, AIProviderRequestError, VisionRequest, AudioInput, Transcription
from src.key_manager import ServiceType


class CloudflareChatProvider(OpenAICompatibleProvider):
    ENDPOINT = "https://api.cloudflare.com/client/v4/accounts"

    def __init__(self, **kwargs: Any):
        kwargs.update(service=ServiceType.CLOUDFLARE, base_url=self.ENDPOINT)
        super().__init__(**kwargs)

    @staticmethod
    def _account(raw: str) -> tuple[str, str]:
        account, separator, token = raw.partition(":")
        if not separator or not re.fullmatch(r"[a-fA-F0-9]{32}", account) or not token or any(c.isspace() for c in token):
            raise AIProviderConfigurationError("CLOUDFLARE_ACCOUNT_INVALID")
        return account, token

    def _completion_url(self, key: str) -> str:
        account, _ = self._account(key)
        return f"{self.ENDPOINT}/{account}/ai/v1/chat/completions"

    def _headers(self, key: str) -> dict[str, str]:
        _, token = self._account(key)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _completion_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Do not wait in Cloudflare's capacity queue while other providers
        # are available. A busy response belongs to the normal fallback path.
        return {**payload, "options": {"rejectIfBusy": True}}

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if request.task not in {"chat", "fast", "analysis", "summary"}:
            raise AIProviderProtocolError("CLOUDFLARE_CHAT_TEXT_ONLY")
        bounded = self._bounded_messages(request.messages)
        if any(not isinstance(m.get("content"), (str, type(None))) for m in bounded):
            raise AIProviderProtocolError("CLOUDFLARE_CHAT_TEXT_ONLY")
        if self._resolve_model(request.model, self.model).startswith("@cf/qwen/qwen3-") and not request.thinking_enabled and request.reasoning_effort in {None, "none"}:
            messages = [dict(m) for m in bounded]
            for message in reversed(messages):
                if message.get("role") == "user" and isinstance(message.get("content"), str):
                    # Qwen3's documented non-thinking switch avoids spending
                    # a short chat's output budget on internal reasoning.
                    message["content"] += "\n/no_think"
                    break
            try:
                self._bounded_messages(messages)
            except AIProviderRequestError:
                messages = bounded
            request = replace(request, messages=messages)
        response = await super().complete(request)
        if request.response_format == {"type": "json_object"} and not response.raw.get("message", {}).get("tool_calls"):
            try:
                value = json.loads(response.content)
            except (TypeError, ValueError) as exc:
                raise AIProviderProtocolError("CLOUDFLARE_JSON_OBJECT_INVALID") from exc
            if not isinstance(value, dict):
                raise AIProviderProtocolError("CLOUDFLARE_JSON_OBJECT_INVALID")
        return response

    async def vision(self, request: VisionRequest) -> CompletionResponse:
        raise AIProviderProtocolError("CLOUDFLARE_CHAT_TEXT_ONLY")

    async def transcribe(self, audio: AudioInput) -> Transcription:
        raise AIProviderProtocolError("CLOUDFLARE_CHAT_TEXT_ONLY")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise AIProviderProtocolError("CLOUDFLARE_CHAT_TEXT_ONLY")
