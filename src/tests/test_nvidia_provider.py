import pytest

from config import Config
from src.key_manager import KeyManager, ServiceType
from src.ai.openai_compatible import OpenAICompatibleProvider
from src.ai.provider_base import CompletionRequest, VisionRequest


class _Response:
    status_code = 200
    text = '{"choices":[{"message":{"role":"assistant","content":"hazirim"}}]}'


class _Client:
    def __init__(self):
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


class _Keys:
    keys = {ServiceType.NVIDIA: ["fixture-nvidia-key"]}

    def __init__(self):
        self.success = None

    def get_key(self, service):
        assert service is ServiceType.NVIDIA
        return "fixture-nvidia-key"

    def report_success(self, service, key):
        self.success = (service, key)

    def report_error(self, service, key, message):
        raise AssertionError(message)


def test_config_defaults_match_nvidia_openai_compatible_endpoint(monkeypatch):
    monkeypatch.setattr(Config, "NVIDIA_URL", "https://integrate.api.nvidia.com/v1")
    monkeypatch.setattr(Config, "NVIDIA_MODEL", "deepseek-ai/deepseek-v4.1-flash")
    assert Config.NVIDIA_URL.endswith("/v1")
    assert Config.NVIDIA_MODEL == "deepseek-ai/deepseek-v4.1-flash"


def test_key_manager_registers_nvidia_key_without_exposing_value(monkeypatch):
    monkeypatch.setattr(Config, "NVIDIA_KEY", "fixture-nvidia-key")
    manager = KeyManager()
    assert manager.keys[ServiceType.NVIDIA] == ["fixture-nvidia-key"]
    assert ServiceType.NVIDIA in manager.key_statuses


@pytest.mark.asyncio
async def test_nvidia_provider_uses_openai_wire_format_and_bounded_payload():
    client = _Client()
    provider = OpenAICompatibleProvider(
        service=ServiceType.NVIDIA,
        base_url="https://integrate.api.nvidia.com/v1",
        model="deepseek-ai/deepseek-v4.1-flash",
        key_manager=_Keys(),
        http_client=client,
    )

    response = await provider.complete(
        CompletionRequest(
            messages=[{"role": "user", "content": "selam"}],
            max_tokens=256,
            reasoning_effort="none",
            thinking_enabled=False,
        )
    )

    assert response.provider == "nvidia"
    assert response.model == "deepseek-ai/deepseek-v4.1-flash"
    assert response.content == "hazirim"
    url, kwargs = client.calls[0]
    assert url == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer fixture-nvidia-key"
    assert kwargs["json"]["model"] == "deepseek-ai/deepseek-v4.1-flash"
    assert kwargs["json"]["max_tokens"] == 256
    assert kwargs["json"]["messages"] == [{"role": "user", "content": "selam"}]
    assert "fixture-nvidia-key" not in repr(response.raw)


@pytest.mark.asyncio
async def test_nvidia_vision_uses_multimodal_wire_format_without_reasoning_payload():
    client = _Client()
    provider = OpenAICompatibleProvider(
        service=ServiceType.NVIDIA,
        base_url="https://integrate.api.nvidia.com/v1",
        model="deepseek-ai/deepseek-v4.1-flash",
        key_manager=_Keys(),
        http_client=client,
        max_media_bytes=1024,
    )

    response = await provider.vision(
        VisionRequest(
            prompt="bu gorseli kisa acikla",
            image_bytes=b"fixture-image",
            mime_type="image/png",
            max_tokens=128,
        )
    )

    assert response.provider == "nvidia"
    url, kwargs = client.calls[0]
    assert url == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert kwargs["json"]["messages"][0]["content"][0]["type"] == "text"
    assert kwargs["json"]["messages"][0]["content"][1]["type"] == "image_url"
    assert kwargs["json"]["max_tokens"] == 128
    assert "reasoning_effort" not in kwargs["json"]


