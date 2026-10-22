import json

import pytest

from src.ai.chatgpt_plan import ChatGPTCliProvider, ChatGPTPlanError, _parse_cli_jsonl
from src.ai.provider_base import CompletionRequest, CompletionResponse
from src.ai.router import AIProviderRouter


class FakeProcess:
    def __init__(self, stdout: bytes, *, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode
        self.prompt = None

    async def communicate(self, prompt: bytes):
        self.prompt = prompt
        return self.stdout, b""

    async def kill(self):
        self.returncode = -9


def _jsonl(*events: dict) -> bytes:
    return ("\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n").encode()


@pytest.mark.asyncio
async def test_cli_provider_uses_headless_codex_exec_and_extracts_final_message(monkeypatch):
    process = FakeProcess(_jsonl(
        {"type": "thread.started", "thread_id": "opaque"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "selam ğŸ‘‹"}},
        {"type": "turn.completed", "usage": {"input_tokens": 7, "output_tokens": 3}},
    ))
    calls = []

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        assert "OPENAI_API_KEY" not in kwargs["env"]
        return process

    monkeypatch.setattr("src.ai.chatgpt_plan.shutil.which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr("src.ai.chatgpt_plan.asyncio.create_subprocess_exec", spawn)
    provider = ChatGPTCliProvider(model=None, timeout_seconds=5, workdir="/tmp")

    result = await provider.complete(CompletionRequest(messages=[
        {"role": "system", "content": "Türkçe ve kısa yaz"},
        {"role": "user", "content": "Naber"},
    ]))

    command = calls[0][0]
    assert command[:3] == ("/usr/bin/codex", "exec", "--json")
    assert "--ephemeral" in command
    assert "--sandbox" in command and "read-only" in command
    assert command[-1] == "-"
    assert b"[system]" in process.prompt and b"[user]" in process.prompt
    assert result.content == "selam ğŸ‘‹"
    assert result.model == "gpt-5.6-luna"
    assert command[command.index("--model") + 1] == "gpt-5.6-luna"
    assert result.usage == {"prompt_tokens": 7, "completion_tokens": 3}


@pytest.mark.asyncio
async def test_cli_failure_falls_back_to_evren(monkeypatch):
    async def spawn(*_args, **_kwargs):
        return FakeProcess(_jsonl({"type": "turn.failed", "error": {"message": "401 Unauthorized"}}), returncode=1)

    monkeypatch.setattr("src.ai.chatgpt_plan.shutil.which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr("src.ai.chatgpt_plan.asyncio.create_subprocess_exec", spawn)
    cli = ChatGPTCliProvider(timeout_seconds=5)

    class Evren:
        name = "evren"

        async def complete(self, _request):
            return CompletionResponse("yedek", "evren", "deepseek")

    router = AIProviderRouter([cli, Evren()], routes={"chat": ["openai", "evren"]}, failure_threshold=1)
    result = await router.complete(CompletionRequest(messages=[{"role": "user", "content": "selam"}]))
    assert result.content == "yedek"
    assert router._opened_until["openai"] > 0


def test_cli_parser_reduces_auth_failures_to_safe_code():
    with pytest.raises(ChatGPTPlanError) as error:
        _parse_cli_jsonl(_jsonl({"type": "error", "message": "401 Unauthorized: secret details"}))
    assert error.value.safe_code == "cli_auth_required"
    assert "secret" not in str(error.value)


def test_cli_rejects_shell_like_model_names():
    with pytest.raises(ValueError, match="MODEL_INVALID"):
        ChatGPTCliProvider(model="gpt; rm -rf /")
