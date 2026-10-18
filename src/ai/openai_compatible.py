"""OpenAI-compatible AI provider adapter.

The adapter owns credentials, request normalization and usage accounting. The
router above it owns task routing and circuit fallback; Telegram handlers never
construct provider HTTP requests directly.
"""

from __future__ import annotations

import asyncio
import time
import json
import base64
import ipaddress
import math
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from src.key_manager import KeyManagerError, ServiceCircuitOpenError, ServiceType

from .provider_base import (
    AudioInput,
    AIProviderRequestError,
    CompletionRequest,
    CompletionResponse,
    Transcription,
    VisionRequest,
)


class AIProviderConfigurationError(ValueError):
    """Raised when a configured OpenAI-compatible endpoint is unsafe."""


class AIProviderProtocolError(ValueError):
    """Raised when an upstream response does not satisfy the typed contract."""


class AIProviderHTTPError(RuntimeError):
    """Safe, structured upstream HTTP failure.

    The upstream response body is intentionally never retained. Operators still
    need to distinguish a transient rate limit from an exhausted quota, while
    Telegram users only receive the generic safe fallback.
    """

    def __init__(self, status_code: int, *, quota_exhausted: bool = False) -> None:
        self.status_code = int(status_code)
        self.quota_exhausted = bool(quota_exhausted)
        code = f"provider_http_{self.status_code}"
        if self.quota_exhausted:
            code += "_quota"
        super().__init__(code)


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        service: ServiceType,
        base_url: str,
        model: str,
        key_manager: Any,
        http_client: Any,
        timeout_seconds: float = 30.0,
        usage_tracker: Any | None = None,
        vision_model: str | None = None,
        transcription_model: str | None = None,
        embedding_model: str | None = None,
        max_media_bytes: int = 12 * 1024 * 1024,
        max_completion_tokens: int = 4096,
        max_message_chars: int = 64_000,
        max_response_chars: int = 64_000,
        allowed_models: Iterable[str] | None = None,
        retry_attempts: int = 0,
        retry_backoff_ms: int = 150,
        fast_timeout_seconds: float | None = None,
    ) -> None:
        self.service = service
        self.name = service.value
        self.base_url = self._validate_base_url(base_url)
        self.model = model
        self.key_manager = key_manager
        self.http_client = http_client
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.fast_timeout_seconds = max(
            0.1,
            min(self.timeout_seconds, float(fast_timeout_seconds or self.timeout_seconds)),
        )
        self.retry_attempts = max(0, min(2, int(retry_attempts)))
        self.retry_backoff_seconds = max(0.025, min(1.0, int(retry_backoff_ms) / 1000))
        self.usage_tracker = usage_tracker
        self.vision_model = vision_model or model
        self.transcription_model = transcription_model or model
        self.embedding_model = embedding_model or model
        self.max_media_bytes = max(1, int(max_media_bytes))
        self.max_completion_tokens = max(1, min(int(max_completion_tokens), 32_000))
        self.max_message_chars = max(1_000, min(int(max_message_chars), 256_000))
        self.max_response_chars = max(1_000, min(int(max_response_chars), 256_000))
        configured_models = allowed_models or (
            self.model,
            self.vision_model,
            self.transcription_model,
            self.embedding_model,
        )
        self.allowed_models = frozenset(
            model_name.strip()
            for model_name in configured_models
            if isinstance(model_name, str) and 1 <= len(model_name.strip()) <= 128
        )
        if not self.allowed_models or self.model not in self.allowed_models:
            raise AIProviderConfigurationError("AI_PROVIDER_MODELS_INVALID")

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        key = None
        started = time.perf_counter()
        try:
            if not self.key_manager.keys.get(self.service):
                raise KeyManagerError(f"No key configured for {self.name}")
            key = self.key_manager.get_key(self.service)
            messages = self._bounded_messages(request.messages)
            selected_model = self._resolve_model(request.model, self.model)
            payload = {
                "model": selected_model,
                "messages": messages,
                "temperature": 0.7,
                "stream": False,
            }
            if request.max_tokens is not None:
                requested_tokens = int(request.max_tokens)
                if requested_tokens < 1:
                    raise AIProviderRequestError("AI_MAX_TOKENS_INVALID")
                payload["max_tokens"] = min(requested_tokens, self.max_completion_tokens)
            if request.tools:
                payload["tools"] = request.tools
                payload["tool_choice"] = "auto"
            if request.response_format is not None:
                payload["response_format"] = dict(request.response_format)
            # Clean APIs exposes DeepSeek V4 through the OpenAI wire format.
            # Make the thinking policy explicit so short chats do not inherit
            # an expensive upstream default. Generic providers keep the
            # portable payload and can still be used as failover targets.
            if self.service in {ServiceType.EVREN, ServiceType.CLEANAPIS, ServiceType.IAMHC}:
                effort = str(request.reasoning_effort or "").strip().casefold()
                if effort in {"none", "low", "high", "max"}:
                    payload["reasoning_effort"] = effort
                    payload["thinking"] = {
                        "type": "enabled" if (request.thinking_enabled is True or effort != "none") else "disabled"
                    }
            response = await self._post_completion(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                timeout=self.fast_timeout_seconds if request.task == "fast" else self.timeout_seconds,
            )
            data = self._json(response)
            self._ensure_ok(response, data)
            message = self._completion_message(data)
            usage = self._usage(data)
            content = self._content_text(message.get("content"))
            if len(content) > self.max_response_chars:
                raise AIProviderProtocolError("AI_RESPONSE_TOO_LARGE")
            self.key_manager.report_success(self.service, key)
            await self._record_usage(True, started, usage)
            return CompletionResponse(
                content=content,
                provider=self.name,
                model=selected_model,
                usage=usage,
                raw={
                    "message": message,
                    "service": self.service,
                    "url": f"{self.base_url}/chat/completions",
                    "timeout": self.timeout_seconds,
                },
            )
        except (ServiceCircuitOpenError, KeyManagerError):
            await self._record_usage(False, started, {})
            raise
        except AIProviderProtocolError as exc:
            if key is not None:
                self._report_provider_error(key, exc)
            await self._record_usage(False, started, {})
            raise
        except ValueError:
            # Request bounds and caller-provided media/schema values are
            # deterministic input failures; they must not cool down a valid
            # provider key or open its circuit.
            await self._record_usage(False, started, {})
            raise
        except Exception as exc:
            if key is not None:
                self._report_provider_error(key, exc)
            await self._record_usage(False, started, {})
            raise

    async def _post_completion(self, url: str, **kwargs: Any) -> Any:
        """Retry bounded upstream overload and transport failures.

        A single EVREN route is common in production. Retrying only HTTP
        responses left connection resets and client-side timeout exceptions to
        immediately trip both the key and router circuits, which surfaced as
        the misleading ``all services busy`` reply. Cancellation is never
        swallowed and the attempt count remains strictly bounded.
        """

        attempts = self.retry_attempts + 1
        for attempt in range(attempts):
            try:
                response = await self.http_client.post(url, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt + 1 >= attempts or not self._is_retryable_transport_error(exc):
                    raise
                await asyncio.sleep(self.retry_backoff_seconds * (attempt + 1))
                continue
            try:
                status_code = int(getattr(response, "status_code", 500))
            except (TypeError, ValueError):
                status_code = 500
            if (
                attempt + 1 < attempts
                and status_code in {408, 425, 429, 500, 502, 503, 504}
            ):
                await asyncio.sleep(self.retry_backoff_seconds * (attempt + 1))
                continue
            return response
        raise RuntimeError("AI_PROVIDER_RETRY_EXHAUSTED")

    @staticmethod
    def _is_retryable_transport_error(error: BaseException) -> bool:
        """Recognise common httpx/aiohttp transport failures without import coupling."""

        name = type(error).__name__.casefold()
        detail = str(error).casefold()
        if isinstance(error, (TimeoutError, ConnectionError, OSError)):
            return True
        return any(
            marker in name or marker in detail
            for marker in (
                "timeout",
                "connecterror",
                "connectionerror",
                "readerror",
                "writeerror",
                "remoteprotocol",
                "networkerror",
                "temporarily unavailable",
            )
        )

    async def _record_usage(self, success: bool, started: float, usage: dict[str, int]) -> None:
        if self.usage_tracker is None:
            return
        await self.usage_tracker.record(
            self.name,
            success=success,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            task="chat",
        )

    async def vision(self, request: VisionRequest | dict[str, Any]) -> CompletionResponse:
        """Call an OpenAI-compatible multimodal chat completion endpoint."""
        if isinstance(request, dict):
            request = VisionRequest(**request)
        image = self._read_media(request.image_path, request.image_bytes, request.mime_type)
        image_url = image if isinstance(image, str) and image.startswith("data:") else (
            f"data:{request.mime_type};base64,{base64.b64encode(image).decode('ascii')}"
        )
        started = time.perf_counter()
        key = None
        try:
            key = self._get_key()
            selected_model = self._resolve_model(request.model, self.vision_model)
            requested_tokens = int(request.max_tokens)
            if requested_tokens < 1:
                raise AIProviderRequestError("AI_MAX_TOKENS_INVALID")
            payload = {
                "model": selected_model,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": request.prompt[:4000]},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]}],
                "max_tokens": min(requested_tokens, self.max_completion_tokens),
                "stream": False,
            }
            # Mimo/DeepSeek-compatible vision endpoints may spend the entire
            # small response budget on hidden reasoning unless this is made
            # explicit. Vision analysis is a fast user-facing path here.
            if self.service in {ServiceType.EVREN, ServiceType.CLEANAPIS, ServiceType.IAMHC}:
                payload["reasoning_effort"] = "none"
            response = await self.http_client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(key),
                timeout=self.timeout_seconds,
            )
            data = self._json(response)
            self._ensure_ok(response, data)
            message = self._completion_message(data)
            content = self._content_text(message.get("content"))
            if len(content) > self.max_response_chars:
                raise AIProviderProtocolError("AI_RESPONSE_TOO_LARGE")
            usage = self._usage(data)
            self.key_manager.report_success(self.service, key)
            await self._record_usage(True, started, usage)
            return CompletionResponse(
                content=content,
                provider=self.name,
                model=selected_model,
                usage=usage,
                raw={"message": message, "service": self.service, "timeout": self.timeout_seconds},
            )
        except (ServiceCircuitOpenError, KeyManagerError):
            await self._record_usage(False, started, {})
            raise
        except AIProviderProtocolError as exc:
            if key is not None:
                self._report_provider_error(key, exc)
            await self._record_usage(False, started, {})
            raise
        except ValueError:
            await self._record_usage(False, started, {})
            raise
        except Exception as exc:
            if key is not None:
                self._report_provider_error(key, exc)
            await self._record_usage(False, started, {})
            raise

    async def transcribe(self, audio: AudioInput | dict[str, Any]) -> Transcription:
        """Transcribe an audio file through ``/audio/transcriptions``."""
        if isinstance(audio, dict):
            audio = AudioInput(**audio)
        payload = self._read_media(audio.audio_path, audio.audio_bytes, audio.mime_type)
        if isinstance(payload, str):
            raise AIProviderRequestError("AUDIO_DATA_URL_NOT_SUPPORTED")
        started = time.perf_counter()
        key = None
        try:
            key = self._get_key()
            selected_model = self._resolve_model(audio.model, self.transcription_model)
            response = await self.http_client.post(
                f"{self.base_url}/audio/transcriptions",
                data={
                    "model": selected_model,
                    **({"language": audio.language} if audio.language else {}),
                },
                files={"file": (audio.filename, payload, audio.mime_type)},
                headers={"Authorization": f"Bearer {key}"},
                timeout=self.timeout_seconds,
            )
            data = self._json(response)
            self._ensure_ok(response, data)
            text = str(data.get("text") or "").strip()
            if not text:
                raise AIProviderProtocolError("EMPTY_TRANSCRIPTION")
            if len(text) > self.max_response_chars:
                raise AIProviderProtocolError("AI_TRANSCRIPTION_TOO_LARGE")
            self.key_manager.report_success(self.service, key)
            usage = {"audio_seconds": int(data.get("duration", 0) or 0)}
            await self._record_usage(True, started, usage)
            return Transcription(text, self.name, selected_model, audio.language, usage)
        except (ServiceCircuitOpenError, KeyManagerError):
            await self._record_usage(False, started, {})
            raise
        except AIProviderProtocolError as exc:
            if key is not None:
                self._report_provider_error(key, exc)
            await self._record_usage(False, started, {})
            raise
        except ValueError:
            await self._record_usage(False, started, {})
            raise
        except Exception as exc:
            if key is not None:
                self._report_provider_error(key, exc)
            await self._record_usage(False, started, {})
            raise

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate bounded embeddings through ``/embeddings``."""
        values = [str(text)[:12000] for text in (texts or [])]
        if not values:
            return []
        if len(values) > 128:
            raise AIProviderRequestError("EMBEDDING_BATCH_TOO_LARGE")
        started = time.perf_counter()
        key = None
        try:
            key = self._get_key()
            response = await self.http_client.post(
                f"{self.base_url}/embeddings",
                json={"model": self._resolve_model(None, self.embedding_model), "input": values},
                headers=self._headers(key),
                timeout=self.timeout_seconds,
            )
            data = self._json(response)
            self._ensure_ok(response, data)
            raw_rows = data.get("data")
            if not isinstance(raw_rows, list) or any(not isinstance(row, dict) for row in raw_rows):
                raise AIProviderProtocolError("INVALID_EMBEDDING_RESPONSE")
            try:
                rows = sorted(raw_rows, key=lambda row: int(row.get("index", 0)))
                vectors = [[float(value) for value in row.get("embedding", [])] for row in rows]
            except (TypeError, ValueError, OverflowError) as exc:
                raise AIProviderProtocolError("INVALID_EMBEDDING_RESPONSE") from exc
            if len(vectors) != len(values) or any(not vector for vector in vectors):
                raise AIProviderProtocolError("INVALID_EMBEDDING_RESPONSE")
            if any(not math.isfinite(value) for vector in vectors for value in vector):
                raise AIProviderProtocolError("INVALID_EMBEDDING_RESPONSE")
            self.key_manager.report_success(self.service, key)
            usage = self._usage(data)
            await self._record_usage(True, started, usage)
            return vectors
        except (ServiceCircuitOpenError, KeyManagerError):
            await self._record_usage(False, started, {})
            raise
        except AIProviderProtocolError as exc:
            if key is not None:
                self._report_provider_error(key, exc)
            await self._record_usage(False, started, {})
            raise
        except ValueError:
            await self._record_usage(False, started, {})
            raise
        except Exception as exc:
            if key is not None:
                self._report_provider_error(key, exc)
            await self._record_usage(False, started, {})
            raise

    def _report_provider_error(self, key: str, error: BaseException) -> None:
        """Report a bounded state code without forwarding provider payloads.

        KeyManager intentionally uses short-lived temporary cooldowns for a
        normal 429 rate limit. Only an upstream response that explicitly
        signals exhausted quota receives the long quota cooldown.
        """

        if isinstance(error, AIProviderHTTPError):
            if error.status_code == 429:
                code = "quota_exhausted" if error.quota_exhausted else "upstream_rate_limited"
            else:
                code = f"provider_http_{error.status_code}"
        elif self._is_retryable_transport_error(error):
            # A timeout/reset is a provider transport event, not evidence
            # that the credential is invalid. The router records provider
            # health and handles fallback; cooling the only valid key here
            # turns one transient outage into a misleading "AI disabled"
            # response for every following request.
            return
        else:
            code = type(error).__name__[:64]
        self.key_manager.report_error(self.service, key, code)

    def _get_key(self) -> str:
        if not self.key_manager.keys.get(self.service):
            raise KeyManagerError(f"No key configured for {self.name}")
        return self.key_manager.get_key(self.service)

    def _resolve_model(self, requested: str | None, default: str) -> str:
        candidate = str(requested or default).strip()
        if candidate not in self.allowed_models:
            raise AIProviderRequestError("AI_MODEL_NOT_ALLOWED")
        return candidate

    @staticmethod
    def _headers(key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    @staticmethod
    def _json(response: Any) -> dict[str, Any]:
        try:
            value = response.json() if hasattr(response, "json") else json.loads(response.text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AIProviderProtocolError("AI_RESPONSE_INVALID_JSON") from exc
        if not isinstance(value, dict):
            raise AIProviderProtocolError("AI_RESPONSE_SCHEMA_INVALID")
        return value

    @staticmethod
    def _ensure_ok(response: Any, data: dict[str, Any]) -> None:
        try:
            status_code = int(getattr(response, "status_code", 500))
        except (TypeError, ValueError):
            raise AIProviderProtocolError("AI_RESPONSE_STATUS_INVALID")
        if status_code < 200 or status_code >= 300:
            # Keep only a boolean quota signal. Error bodies may contain API
            # keys, request IDs or user data and must never enter logs/state.
            error = data.get("error") if isinstance(data, dict) else None
            try:
                signal = json.dumps(error, ensure_ascii=False, default=str)[:512].casefold()
            except (TypeError, ValueError):
                signal = ""
            quota_exhausted = any(
                marker in signal
                for marker in ("insufficient_quota", "quota_exceeded", "billing", "credits exhausted")
            )
            raise AIProviderHTTPError(status_code, quota_exhausted=quota_exhausted)

    @staticmethod
    def _completion_message(data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise AIProviderProtocolError("AI_RESPONSE_CHOICES_INVALID")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise AIProviderProtocolError("AI_RESPONSE_MESSAGE_INVALID")
        return message

    def _bounded_messages(self, messages: Any) -> list[dict[str, Any]]:
        if not isinstance(messages, list) or not messages or len(messages) > 64:
            raise AIProviderRequestError("AI_MESSAGES_INVALID")
        bounded: list[dict[str, Any]] = []
        total_chars = 0
        for item in messages:
            if not isinstance(item, dict):
                raise AIProviderRequestError("AI_MESSAGE_INVALID")
            role = item.get("role")
            if role not in {"system", "user", "assistant", "tool"}:
                raise AIProviderRequestError("AI_MESSAGE_ROLE_INVALID")
            try:
                serialized = json.dumps(item, ensure_ascii=False, default=str)
            except (TypeError, ValueError) as exc:
                raise AIProviderRequestError("AI_MESSAGE_INVALID") from exc
            if len(serialized) > self.max_message_chars:
                raise AIProviderRequestError("AI_MESSAGE_TOO_LARGE")
            total_chars += len(serialized)
            if total_chars > self.max_message_chars * 4:
                raise AIProviderRequestError("AI_MESSAGES_TOO_LARGE")
            bounded.append(dict(item))
        return bounded

    @staticmethod
    def _validate_base_url(value: str) -> str:
        raw = str(value or "").strip().rstrip("/")
        try:
            parsed = urlsplit(raw)
            host = (parsed.hostname or "").casefold().rstrip(".")
            port = parsed.port
        except ValueError as exc:
            raise AIProviderConfigurationError("AI_PROVIDER_URL_INVALID") from exc
        private_host = host in {"localhost", "localhost.localdomain"} or host.endswith(".local")
        try:
            address = ipaddress.ip_address(host)
            private_host = private_host or (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_reserved
                or address.is_multicast
            )
        except ValueError:
            pass
        valid = (
            parsed.scheme == "https"
            and bool(host)
            and not private_host
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and (port is None or 1 <= port <= 65535)
        )
        if not valid:
            raise AIProviderConfigurationError("AI_PROVIDER_URL_INVALID")
        return raw

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict)).strip()
        return str(content or "")

    @staticmethod
    def _usage(data: dict[str, Any]) -> dict[str, int]:
        usage = data.get("usage") or {}
        try:
            prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise AIProviderProtocolError("AI_USAGE_INVALID") from exc
        if prompt_tokens < 0 or completion_tokens < 0:
            raise AIProviderProtocolError("AI_USAGE_INVALID")
        return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}

    def _read_media(self, path: str | None, raw: bytes | None, mime_type: str) -> bytes | str:
        if raw is not None:
            value = bytes(raw)
        elif path:
            candidate = Path(path)
            if not candidate.is_file() or candidate.is_symlink():
                raise AIProviderRequestError("MEDIA_FILE_NOT_ALLOWED")
            if candidate.stat().st_size > self.max_media_bytes:
                raise AIProviderRequestError("MEDIA_FILE_TOO_LARGE")
            value = candidate.read_bytes()
        else:
            raise AIProviderRequestError("MEDIA_INPUT_REQUIRED")
        if len(value) > self.max_media_bytes:
            raise AIProviderRequestError("MEDIA_FILE_TOO_LARGE")
        if not value:
            raise AIProviderRequestError("MEDIA_FILE_EMPTY")
        return value

    async def health_check(self) -> dict[str, Any]:
        configured = bool(self.key_manager.keys.get(self.service))
        return {"name": self.name, "healthy": configured, "detail": "configured" if configured else "missing_key"}
