"""Sign in with ChatGPT plan-usage provider for the Responses API.

This adapter stores OAuth credentials separately from API-key providers and
keeps OpenAI's preview restrictions (streaming, no server-side storage, full
request history) at the provider boundary.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from src.key_manager import ServiceType

from .provider_base import (
    AIProviderRequestError,
    CompletionRequest,
    CompletionResponse,
)

logger = logging.getLogger(__name__)

OAUTH_TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
RESPONSES_URL = "https://api.openai.com/v1/responses"
MODELS_URL = "https://api.openai.com/v1/models"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
RESOURCE_SCOPE = "resource.invoke"
MAX_INPUT_ITEMS = 128
MAX_TOOL_CALLS = 8
MAX_ARGUMENT_CHARS = 16_384
MAX_MESSAGE_CHARS = 256_000
MAX_RESPONSE_CHARS = 64_000
MAX_CLI_PROMPT_CHARS = 256_000
MAX_CLI_OUTPUT_CHARS = 64_000


class ChatGPTPlanError(RuntimeError):
    """Safe provider error; never retain upstream response bodies or tokens."""

    def __init__(
        self,
        code: str,
        *,
        status_code: int | None = None,
        circuit_cooldown_seconds: float | None = None,
    ) -> None:
        self.safe_code = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(code))[:96]
        self.status_code = status_code
        self.circuit_cooldown_seconds = circuit_cooldown_seconds
        super().__init__(self.safe_code)


def _cli_model_arg(model: str | None) -> str | None:
    """Validate an optional Codex CLI model without accepting shell syntax."""

    if model is None:
        return None
    value = str(model).strip()
    if not value:
        return None
    if len(value) > 128 or any(char.isspace() for char in value):
        raise ValueError("CHATGPT_CLI_MODEL_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9._:/-]+", value):
        raise ValueError("CHATGPT_CLI_MODEL_INVALID")
    return value


def _cli_prompt(messages: list[dict[str, Any]], response_format: dict[str, Any] | None) -> str:
    """Serialize a completion request into a bounded, labelled CLI prompt.

    The Codex CLI does not accept OpenAI function-tool payloads. Roles are kept
    explicit so a user message cannot silently become a system instruction.
    """

    if not isinstance(messages, list) or not messages or len(messages) > 64:
        raise AIProviderRequestError("CHATGPT_MESSAGES_INVALID")
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            raise AIProviderRequestError("CHATGPT_MESSAGE_INVALID")
        role = str(message.get("role") or "").strip().lower()
        if role not in {"system", "developer", "user", "assistant"}:
            # Tool calls require the Responses API adapter; the CLI route must
            # fail as a provider error so the router can use a typed fallback.
            if role == "tool":
                raise ChatGPTPlanError("cli_tool_history_unsupported")
            raise AIProviderRequestError("CHATGPT_MESSAGE_ROLE_INVALID")
        content = message.get("content", "")
        if isinstance(content, list):
            # Text-only CLI mode deliberately does not guess at multimodal
            # content. Vision requests have their own provider contract.
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
                    value = item.get("text")
                    if isinstance(value, str):
                        text_parts.append(value)
            content = "\n".join(text_parts)
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, default=str)
        if len(content) > MAX_MESSAGE_CHARS:
            raise AIProviderRequestError("CHATGPT_MESSAGE_TOO_LARGE")
        parts.append(f"[{role}]\n{content}")
    if response_format == {"type": "json_object"}:
        parts.append(
            "[output_format]\nReturn only one valid JSON object. Do not wrap it in Markdown fences."
        )
    prompt = "\n\n".join(parts)
    if len(prompt) > MAX_CLI_PROMPT_CHARS:
        raise AIProviderRequestError("CHATGPT_CLI_PROMPT_TOO_LARGE")
    return prompt


def _cli_text_from_value(value: Any) -> str:
    """Extract text from Codex JSONL item shapes without exposing raw events."""

    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "content", "message"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
            if isinstance(candidate, (dict, list)):
                nested = _cli_text_from_value(candidate)
                if nested:
                    return nested
    if isinstance(value, list):
        return "".join(_cli_text_from_value(item) for item in value)
    return ""


def _parse_cli_jsonl(stdout: bytes) -> tuple[str, dict[str, int]]:
    """Parse `codex exec --json` JSONL and return only the final answer."""

    text = stdout.decode("utf-8", "replace")
    if len(text) > MAX_CLI_OUTPUT_CHARS * 4:
        raise ChatGPTPlanError("cli_output_too_large")
    answer = ""
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    saw_event = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            # The CLI may emit a final plain-text line in non-JSON builds. It
            # is accepted only when no structured events were seen.
            if not saw_event and len(line) <= MAX_CLI_OUTPUT_CHARS:
                answer = line
            continue
        if not isinstance(event, dict):
            continue
        saw_event = True
        event_type = str(event.get("type") or "")
        if event_type in {"error", "turn.failed"}:
            message = _cli_text_from_value(event.get("error") or event.get("message"))
            lowered = message.casefold()
            if "401" in lowered or "unauthorized" in lowered or "authentication" in lowered:
                raise ChatGPTPlanError("cli_auth_required", status_code=401, circuit_cooldown_seconds=120.0)
            if "429" in lowered or "rate limit" in lowered or "quota" in lowered:
                raise ChatGPTPlanError("cli_rate_limited", status_code=429, circuit_cooldown_seconds=30.0)
            raise ChatGPTPlanError("cli_request_failed")
        if event_type == "item.completed":
            item = event.get("item")
            item_type = item.get("type") if isinstance(item, dict) else None
            if item_type in {"agent_message", "assistant_message", "message"}:
                candidate = _cli_text_from_value(item)
                if candidate:
                    answer = candidate
        if event_type == "turn.completed":
            raw_usage = event.get("usage")
            if isinstance(raw_usage, dict):
                try:
                    usage["prompt_tokens"] = max(0, int(raw_usage.get("input_tokens", 0) or 0))
                    usage["completion_tokens"] = max(0, int(raw_usage.get("output_tokens", 0) or 0))
                except (TypeError, ValueError):
                    pass
    answer = answer.strip()
    if not answer:
        raise ChatGPTPlanError("cli_empty_response")
    if len(answer) > MAX_CLI_OUTPUT_CHARS:
        raise ChatGPTPlanError("cli_response_too_large")
    return answer, usage


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _safe_error_code(payload: Any, fallback: str) -> str:
    """Extract only known machine codes from an untrusted upstream payload."""

    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if not isinstance(code, str):
        return fallback
    normalized = code.strip().casefold()
    allowed = {
        "subscription_sharing_usage_limit_exceeded",
        "subscription_sharing_user_not_eligible",
        "subscription_sharing_usage_unavailable",
        "subscription_sharing_unsupported_capability",
        "invalid_grant",
        "invalid_token",
        "insufficient_quota",
        "rate_limit_exceeded",
        "model_not_found",
        "unsupported_model",
        "model_not_supported",
    }
    return normalized if normalized in allowed else fallback


def _load_record(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ChatGPTPlanError("credentials_path_unsafe")
    try:
        metadata = path.stat()
        if metadata.st_size > 64_000:
            raise ChatGPTPlanError("credentials_record_too_large")
        if os.name != "nt" and metadata.st_mode & 0o077:
            raise ChatGPTPlanError("credentials_permissions_unsafe")
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ChatGPTPlanError("credentials_missing") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ChatGPTPlanError("credentials_unreadable") from exc
    if not isinstance(value, dict):
        raise ChatGPTPlanError("credentials_record_invalid")
    required = ("client_id", "access_token", "refresh_token", "scopes", "expires_at")
    if any(not value.get(name) for name in required):
        raise ChatGPTPlanError("credentials_record_invalid")
    scopes = value.get("scopes")
    if not isinstance(scopes, list) or PLAN_SCOPE not in scopes or RESOURCE_SCOPE not in scopes:
        raise ChatGPTPlanError("plan_scope_missing")
    return value


def credentials_file_ready(path: str | os.PathLike[str]) -> bool:
    """Return whether a protected, plan-authorized SIWC record is loadable."""

    try:
        _load_record(Path(path))
        return True
    except ChatGPTPlanError:
        return False


def _atomic_write_record(path: Path, record: dict[str, Any]) -> None:
    if path.is_symlink():
        raise ChatGPTPlanError("credentials_path_unsafe")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    descriptor = None
    try:
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Windows permissions are managed by the containing ACL-protected
            # directory; chmod is still enforced on Unix deployments.
            if os.name != "nt":
                raise
    except OSError as exc:
        raise ChatGPTPlanError("credentials_write_failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


class ChatGPTPlanCredentials:
    """Refresh a rotating SIWC credential safely across bot and worker."""

    def __init__(self, path: str | os.PathLike[str], http_client: Any, *, timeout_seconds: float = 15.0):
        self.path = Path(path)
        self.http_client = http_client
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._async_lock = asyncio.Lock()

    @asynccontextmanager
    async def _file_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        lock_file = await asyncio.to_thread(open, lock_path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if lock_file.seek(0, os.SEEK_END) == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                deadline = time.monotonic() + self.timeout_seconds
                while True:
                    try:
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise ChatGPTPlanError("credentials_lock_timeout")
                        await asyncio.sleep(0.05)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                deadline = time.monotonic() + self.timeout_seconds
                while True:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise ChatGPTPlanError("credentials_lock_timeout")
                        await asyncio.sleep(0.05)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            await asyncio.to_thread(lock_file.close)

    async def access_token(self) -> str:
        async with self._async_lock, self._file_lock():
            record = _load_record(self.path)
            now = time.time()
            # Refresh close to expiry, avoiding both an expired token and
            # unnecessary rotating-refresh-token exchanges.
            if float(record.get("expires_at", 0)) > now + 90:
                return str(record["access_token"])
            return await self._refresh_locked(record)

    async def refresh_after_rejection(self, rejected_access_token: str) -> str:
        """Retry refresh once after an early-expired access token response."""

        async with self._async_lock, self._file_lock():
            record = _load_record(self.path)
            current = str(record.get("access_token") or "")
            # Another worker may already have rotated and persisted the
            # token while this request was in flight.
            if current and current != rejected_access_token:
                return current
            return await self._refresh_locked(record)

    async def _refresh_locked(self, record: dict[str, Any]) -> str:
        data = {
            "grant_type": "refresh_token",
            "client_id": str(record["client_id"]),
            "refresh_token": str(record["refresh_token"]),
            "resource": "https://api.openai.com/v1",
        }
        try:
            response = await self.http_client.post(
                OAUTH_TOKEN_URL,
                data=data,
                headers={"Accept": "application/json"},
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
        except Exception as exc:
            raise ChatGPTPlanError("token_refresh_transport_error") from exc
        if int(getattr(response, "status_code", 0)) != 200:
            payload = _json_or_empty(response)
            code = _safe_error_code(payload, f"token_refresh_http_{getattr(response, 'status_code', 0)}")
            status = getattr(response, "status_code", None)
            cooldown = _cooldown_for(code, status)
            raise ChatGPTPlanError(code, status_code=status, circuit_cooldown_seconds=cooldown)
        payload = _json_or_empty(response)
        access = payload.get("access_token")
        refresh = payload.get("refresh_token")
        expires = payload.get("expires_in")
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            raise ChatGPTPlanError("token_refresh_response_invalid")
        try:
            expires_seconds = max(60, min(86_400, int(expires)))
        except (TypeError, ValueError):
            raise ChatGPTPlanError("token_refresh_response_invalid")
        scopes = payload.get("scope")
        if isinstance(scopes, str):
            scopes = sorted(set(scopes.split()))
        if not isinstance(scopes, list) or PLAN_SCOPE not in scopes or RESOURCE_SCOPE not in scopes:
            raise ChatGPTPlanError("token_refresh_scope_missing")
        updated = dict(record)
        updated.update({
            "access_token": access,
            "refresh_token": refresh,
            "scopes": scopes,
            "expires_at": time.time() + expires_seconds,
            "expires_in": expires_seconds,
            "saved_at": time.time(),
        })
        _atomic_write_record(self.path, updated)
        return access


def _json_or_empty(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, TypeError, httpx.DecodingError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _response_input(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    inputs: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        if role in {"system", "developer"}:
            if isinstance(content, str) and content.strip():
                instructions.append(content)
            else:
                raise AIProviderRequestError("CHATGPT_SYSTEM_CONTENT_INVALID")
        elif role in {"user", "assistant"}:
            if "tool_calls" in message and role == "assistant":
                calls = message.get("tool_calls") or []
                if not isinstance(calls, list) or len(calls) > MAX_TOOL_CALLS:
                    raise AIProviderRequestError("CHATGPT_TOOL_CALLS_INVALID")
                for call in calls:
                    function = call.get("function") if isinstance(call, dict) else None
                    call_id = call.get("id") if isinstance(call, dict) else None
                    name = function.get("name") if isinstance(function, dict) else None
                    arguments = function.get("arguments") if isinstance(function, dict) else None
                    if (
                        not isinstance(call_id, str) or not call_id or len(call_id) > 128
                        or not isinstance(name, str) or not name or len(name) > 128
                        or not isinstance(arguments, str) or len(arguments) > MAX_ARGUMENT_CHARS
                    ):
                        raise AIProviderRequestError("CHATGPT_TOOL_CALLS_INVALID")
                    try:
                        parsed = json.loads(arguments)
                    except (TypeError, ValueError) as exc:
                        raise AIProviderRequestError("CHATGPT_TOOL_CALLS_INVALID") from exc
                    if not isinstance(parsed, dict):
                        raise AIProviderRequestError("CHATGPT_TOOL_CALLS_INVALID")
                    item = {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                    }
                    namespace = call.get("namespace")
                    if isinstance(namespace, str) and namespace:
                        item["namespace"] = namespace
                    inputs.append(item)
            elif isinstance(content, (str, list)):
                inputs.append({"role": role, "content": content})
            else:
                raise AIProviderRequestError("CHATGPT_MESSAGE_CONTENT_INVALID")
        elif role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id or len(call_id) > 128:
                raise AIProviderRequestError("CHATGPT_TOOL_OUTPUT_INVALID")
            output = message.get("content", "")
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False, separators=(",", ":"), default=str)
            if len(output) > MAX_MESSAGE_CHARS:
                raise AIProviderRequestError("CHATGPT_TOOL_OUTPUT_TOO_LARGE")
            item = {"type": "function_call_output", "call_id": call_id, "output": output}
            if isinstance(message.get("name"), str):
                item["namespace"] = "arel"
            inputs.append(item)
        else:
            raise AIProviderRequestError("CHATGPT_MESSAGE_ROLE_INVALID")
    if not inputs or len(inputs) > MAX_INPUT_ITEMS:
        raise AIProviderRequestError("CHATGPT_INPUT_BOUNDS_INVALID")
    return "\n\n".join(instructions), inputs


def _response_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not tools:
        return []
    normalized: list[dict[str, Any]] = []
    for item in tools:
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict):
            raise AIProviderRequestError("CHATGPT_TOOL_SCHEMA_INVALID")
        name = function.get("name")
        parameters = function.get("parameters")
        if not isinstance(name, str) or not name or len(name) > 128 or not isinstance(parameters, dict):
            raise AIProviderRequestError("CHATGPT_TOOL_SCHEMA_INVALID")
        normalized.append({
            "type": "function",
            "name": name,
            "description": str(function.get("description") or "")[:2_000],
            "parameters": parameters,
            "strict": bool(function.get("strict", False)),
        })
    if len(normalized) > 64:
        raise AIProviderRequestError("CHATGPT_TOOL_COUNT_INVALID")
    # The plan-usage endpoint supports local function calls through a namespace.
    return [{
        "type": "namespace",
        "name": "arel",
        "description": "Arel Telegram bot actions. Use only for an explicit user request.",
        "tools": normalized,
    }]


def _message_from_response(response_data: dict[str, Any]) -> dict[str, Any]:
    output = response_data.get("output")
    if not isinstance(output, list):
        raise ChatGPTPlanError("response_output_invalid")
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                        text = part.get("text")
                        if isinstance(text, str):
                            text_parts.append(text)
        elif item_type == "function_call":
            call_id = item.get("call_id")
            name = item.get("name")
            arguments = item.get("arguments")
            if (
                not isinstance(call_id, str) or not call_id or len(call_id) > 128
                or not isinstance(name, str) or not name or len(name) > 128
                or not isinstance(arguments, str) or len(arguments) > MAX_ARGUMENT_CHARS
            ):
                raise ChatGPTPlanError("response_tool_call_invalid")
            try:
                parsed = json.loads(arguments)
            except (TypeError, ValueError) as exc:
                raise ChatGPTPlanError("response_tool_call_invalid") from exc
            if not isinstance(parsed, dict):
                raise ChatGPTPlanError("response_tool_call_invalid")
            call = {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
            namespace = item.get("namespace")
            if isinstance(namespace, str) and namespace:
                call["namespace"] = namespace
            tool_calls.append(call)
    content = "".join(text_parts)
    if len(content) > MAX_RESPONSE_CHARS:
        raise ChatGPTPlanError("response_text_too_large")
    if not content.strip() and not tool_calls:
        raise ChatGPTPlanError("response_empty")
    if len(tool_calls) > MAX_TOOL_CALLS:
        raise ChatGPTPlanError("response_tool_call_count_invalid")
    return {"role": "assistant", "content": content, **({"tool_calls": tool_calls} if tool_calls else {})}


def _usage(response_data: dict[str, Any]) -> dict[str, int]:
    raw = response_data.get("usage")
    if not isinstance(raw, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0}
    try:
        input_tokens = max(0, int(raw.get("input_tokens", 0) or 0))
        output_tokens = max(0, int(raw.get("output_tokens", 0) or 0))
    except (TypeError, ValueError):
        input_tokens = output_tokens = 0
    return {"prompt_tokens": input_tokens, "completion_tokens": output_tokens}


class ChatGPTPlanProvider:
    """OpenAI Responses API provider authorized by a ChatGPT plan OAuth grant."""

    ALLOWED_MODELS = frozenset({"gpt-5.6-luna", "gpt-6-luna"})

    def __init__(
        self,
        *,
        model: str,
        fallback_model: str = "gpt-6-luna",
        credentials_path: str | os.PathLike[str],
        http_client: Any,
        timeout_seconds: float = 30.0,
        usage_tracker: Any | None = None,
    ) -> None:
        if model not in self.ALLOWED_MODELS or fallback_model not in self.ALLOWED_MODELS:
            raise ValueError("CHATGPT_MODEL_NOT_ALLOWED")
        self.name = ServiceType.OPENAI.value
        self.service = ServiceType.OPENAI
        self.model = model.strip()
        self.fallback_model = fallback_model
        self.credentials = ChatGPTPlanCredentials(credentials_path, http_client, timeout_seconds=timeout_seconds)
        self.http_client = http_client
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.fast_timeout_seconds = min(self.timeout_seconds, 12.0)
        self.usage_tracker = usage_tracker

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        started = time.perf_counter()
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        try:
            model = self.model
            if request.model is not None and request.model != self.model:
                if request.model not in self.ALLOWED_MODELS:
                    raise AIProviderRequestError("CHATGPT_MODEL_NOT_ALLOWED")
                model = request.model
            messages = request.messages
            if not isinstance(messages, list) or not messages or len(messages) > 64:
                raise AIProviderRequestError("CHATGPT_MESSAGES_INVALID")
            total_chars = 0
            for message in messages:
                if not isinstance(message, dict):
                    raise AIProviderRequestError("CHATGPT_MESSAGE_INVALID")
                total_chars += len(json.dumps(message, ensure_ascii=False, default=str))
                if total_chars > MAX_MESSAGE_CHARS * 4:
                    raise AIProviderRequestError("CHATGPT_MESSAGES_TOO_LARGE")
            instructions, input_items = _response_input(messages)
            payload: dict[str, Any] = {
                "model": model,
                "input": input_items,
                "store": False,
                "stream": True,
                "reasoning": {"effort": "none"},
            }
            if instructions:
                payload["instructions"] = instructions
            if request.tools:
                payload["tools"] = _response_tools(request.tools)
                payload["tool_choice"] = "auto"
            if request.response_format is not None:
                if request.response_format != {"type": "json_object"}:
                    raise AIProviderRequestError("CHATGPT_RESPONSE_FORMAT_UNSUPPORTED")
                payload["text"] = {"format": {"type": "json_object"}}
            token = await self.credentials.access_token()
            models = [model]
            if model == "gpt-5.6-luna" and self.fallback_model == "gpt-6-luna":
                models.append(self.fallback_model)
            for index, candidate in enumerate(models):
                candidate_payload = {**payload, "model": candidate}
                try:
                    try:
                        result, usage = await self._stream_response(candidate, token, candidate_payload, request.task)
                    except ChatGPTPlanError as exc:
                        if exc.status_code != 401:
                            raise
                        token = await self.credentials.refresh_after_rejection(token)
                        result, usage = await self._stream_response(candidate, token, candidate_payload, request.task)
                except ChatGPTPlanError as exc:
                    # An application/account permission denial is never a
                    # reason to change clients or attempt another credential.
                    if index == 0 and len(models) > 1 and exc.status_code in {400, 404} and exc.safe_code in {
                        "model_not_found", "unsupported_model", "model_not_supported",
                    }:
                        continue
                    raise
                model = candidate
                break
            await self._record_usage(True, started, usage, request.task)
            return CompletionResponse(
                content=result["content"],
                provider=self.name,
                model=model,
                usage=usage,
                raw={
                    "message": result,
                    "service": self.service,
                    "url": RESPONSES_URL,
                    "timeout": self.timeout_seconds,
                    "key": None,
                },
            )
        except AIProviderRequestError:
            await self._record_usage(False, started, {}, request.task)
            raise
        except ChatGPTPlanError:
            await self._record_usage(False, started, {}, request.task)
            raise
        except Exception as exc:
            await self._record_usage(False, started, {}, request.task)
            if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                raise ChatGPTPlanError("provider_timeout") from exc
            raise ChatGPTPlanError("provider_transport_error") from exc

    async def _stream_response(
        self,
        model: str,
        token: str,
        payload: dict[str, Any],
        task: str,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        timeout = self.fast_timeout_seconds if task == "fast" else self.timeout_seconds
        try:
            stream_context = self.http_client.stream(
                "POST",
                RESPONSES_URL,
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=timeout,
                follow_redirects=False,
            )
            async with stream_context as response:
                status = int(getattr(response, "status_code", 0))
                if status != 200:
                    body = await _read_error_body(response)
                    code = _safe_error_code(body, f"provider_http_{status}")
                    cooldown = _cooldown_for(code, status)
                    raise ChatGPTPlanError(code, status_code=status, circuit_cooldown_seconds=cooldown)
                completed: dict[str, Any] | None = None
                stream_error: ChatGPTPlanError | None = None
                async for line in response.aiter_lines():
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except (TypeError, ValueError):
                        raise ChatGPTPlanError("response_stream_invalid")
                    if not isinstance(event, dict):
                        continue
                    kind = event.get("type")
                    if kind == "response.completed":
                        candidate = event.get("response")
                        if not isinstance(candidate, dict):
                            raise ChatGPTPlanError("response_completed_invalid")
                        completed = candidate
                    elif kind in {"response.failed", "error"}:
                        candidate = event.get("response") if isinstance(event.get("response"), dict) else event
                        error = candidate.get("error") if isinstance(candidate, dict) else None
                        code = _safe_error_code({"error": error}, "response_failed")
                        cooldown = _cooldown_for(code, None)
                        stream_error = ChatGPTPlanError(code, circuit_cooldown_seconds=cooldown)
                    elif kind == "response.incomplete":
                        stream_error = ChatGPTPlanError("response_incomplete")
                if stream_error is not None:
                    raise stream_error
                if completed is None:
                    raise ChatGPTPlanError("response_stream_incomplete")
                message = _message_from_response(completed)
                usage = _usage(completed)
                return message, usage
        except ChatGPTPlanError:
            raise
        except Exception as exc:
            if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                raise ChatGPTPlanError("provider_timeout") from exc
            raise ChatGPTPlanError("provider_transport_error") from exc

    async def _record_usage(self, success: bool, started: float, usage: dict[str, int], task: str) -> None:
        if self.usage_tracker is None:
            return
        await self.usage_tracker.record(
            self.name,
            success=success,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            task=task,
        )

    async def health_check(self) -> dict[str, Any]:
        try:
            record = _load_record(self.credentials.path)
        except ChatGPTPlanError as exc:
            return {"healthy": False, "detail": exc.safe_code}
        if not record.get("refresh_token"):
            return {"healthy": False, "detail": "refresh_token_missing"}
        return {"healthy": True}


class ChatGPTCliProvider:
    """Headless provider backed by the user's normal `codex exec` binary.

    This path intentionally does not use the SIWC/Responses API grant. The
    Codex CLI owns authentication (normally through ``CODEX_HOME``), while
    Arel only supplies a bounded prompt and consumes the final JSONL answer.
    Any CLI/account failure is raised as ``ChatGPTPlanError`` so the shared AI
    router can immediately try EVREN or another configured provider.
    """

    ALLOWED_MODELS = frozenset({"gpt-5.6-luna", "gpt-6-luna"})
    needs_startup_budget = True

    def __init__(
        self,
        *,
        model: str | None = None,
        cli_bin: str = "codex",
        timeout_seconds: float = 30.0,
        workdir: str | os.PathLike[str] | None = None,
        codex_home: str | os.PathLike[str] | None = None,
        usage_tracker: Any | None = None,
    ) -> None:
        self.name = ServiceType.OPENAI.value
        self.service = ServiceType.OPENAI
        self.model = _cli_model_arg(model) or "gpt-5.6-luna"
        if self.model not in self.ALLOWED_MODELS:
            raise ValueError("CHATGPT_CLI_MODEL_NOT_ALLOWED")
        self.cli_bin = str(cli_bin or "codex").strip()
        if not self.cli_bin or len(self.cli_bin) > 512:
            raise ValueError("CHATGPT_CLI_BINARY_INVALID")
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.fast_timeout_seconds = min(self.timeout_seconds, 12.0)
        self.workdir = Path(workdir or tempfile.gettempdir()).expanduser()
        self.codex_home = str(codex_home or "").strip() or None
        self.usage_tracker = usage_tracker

    def _binary(self) -> str:
        path = Path(self.cli_bin).expanduser()
        if path.is_absolute():
            resolved = str(path)
        else:
            resolved = shutil.which(self.cli_bin) or ""
        if not resolved:
            raise ChatGPTPlanError("cli_not_found")
        return resolved

    def _command(self, request: CompletionRequest) -> list[str]:
        command = [
            self._binary(),
            "exec",
            "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
        ]
        model = _cli_model_arg(request.model) if request.model else self.model
        if model not in self.ALLOWED_MODELS:
            raise AIProviderRequestError("CHATGPT_CLI_MODEL_NOT_ALLOWED")
        if model:
            command.extend(["--model", model])
        command.append("-")
        return command

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        started = time.perf_counter()
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        if request.tools:
            # Tool execution belongs to the typed provider adapters. Let the
            # router select one instead of silently pretending the CLI called
            # a tool it cannot represent.
            raise ChatGPTPlanError("cli_tools_unsupported")
        prompt = _cli_prompt(request.messages, request.response_format)
        timeout = self.fast_timeout_seconds if request.task == "fast" else self.timeout_seconds
        try:
            self.workdir.mkdir(parents=True, exist_ok=True)
            command = self._command(request)
            env = os.environ.copy()
            # The normal Codex login is the source of truth. Do not let a
            # generic OpenAI API key silently switch this provider to API-key
            # billing when a login is configured.
            env.pop("OPENAI_API_KEY", None)
            env["NO_COLOR"] = "1"
            if self.codex_home:
                env["CODEX_HOME"] = self.codex_home
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self.workdir),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=(os.name != "nt"),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode("utf-8")),
                    timeout=timeout,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                try:
                    if os.name != "nt" and getattr(process, "pid", None):
                        import signal
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                await asyncio.wait_for(asyncio.shield(process.communicate()), timeout=5.0)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise ChatGPTPlanError("provider_timeout") from exc
            if process.returncode not in (0, None):
                # stderr is deliberately reduced to a category; account
                # details, URLs and prompts must never reach logs or callers.
                error_text = stderr.decode("utf-8", "replace").casefold()
                if "401" in error_text or "unauthorized" in error_text or "authentication" in error_text:
                    raise ChatGPTPlanError("cli_auth_required", status_code=401, circuit_cooldown_seconds=120.0)
                if "429" in error_text or "rate limit" in error_text or "quota" in error_text:
                    raise ChatGPTPlanError("cli_rate_limited", status_code=429, circuit_cooldown_seconds=30.0)
                raise ChatGPTPlanError("cli_process_failed")
            content, usage = _parse_cli_jsonl(stdout)
            await self._record_usage(True, started, usage, request.task)
            return CompletionResponse(
                content=content,
                provider=self.name,
                model=request.model or self.model,
                usage=usage,
                raw={
                    "message": {"role": "assistant", "content": content},
                    "service": self.service,
                    "mode": "cli",
                    "cli_version": None,
                    "key": None,
                },
            )
        except AIProviderRequestError:
            await self._record_usage(False, started, {}, request.task)
            raise
        except ChatGPTPlanError:
            await self._record_usage(False, started, {}, request.task)
            raise
        except (OSError, UnicodeError) as exc:
            await self._record_usage(False, started, {}, request.task)
            raise ChatGPTPlanError("cli_process_unavailable") from exc

    async def _record_usage(self, success: bool, started: float, usage: dict[str, int], task: str) -> None:
        if self.usage_tracker is None:
            return
        await self.usage_tracker.record(
            self.name,
            success=success,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            task=task,
        )

    async def health_check(self) -> dict[str, Any]:
        try:
            path = self._binary()
        except ChatGPTPlanError as exc:
            return {"healthy": False, "detail": exc.safe_code}
        return {"healthy": True, "detail": "cli_ready", "binary": Path(path).name}


async def _read_error_body(response: Any) -> dict[str, Any]:
    try:
        raw = await response.aread()
        if len(raw) > 64_000:
            raw = raw[:64_000]
        data = json.loads(raw.decode("utf-8", "replace"))
    except (httpx.HTTPError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _cooldown_for(code: str, status_code: int | None) -> float | None:
    """Pause known account-level failures without guessing quota reset times."""

    if code == "subscription_sharing_usage_limit_exceeded":
        return 3600.0
    if code == "subscription_sharing_usage_unavailable":
        return 30.0
    if code == "subscription_sharing_user_not_eligible":
        return 86_400.0
    if status_code == 401 or code == "invalid_grant":
        return 900.0
    return None

