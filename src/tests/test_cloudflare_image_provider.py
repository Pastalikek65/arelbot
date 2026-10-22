from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from src.ai.providers.cloudflare_image import (
    CloudflareImageProvider,
    ImageGenerationRequest,
    ImageProviderError,
)
from src.image_router import ImageRouter
from src.key_manager import ServiceType, ServiceCircuitOpenError


class FakeKeys:
    def __init__(self, keys: list[str]):
        self.keys = {ServiceType.CLOUDFLARE: keys}
        self.index = 0
        self.successes = []
        self.errors = []

    def get_key(self, service_type):
        keys = self.keys[service_type]
        key = keys[self.index % len(keys)]
        self.index += 1
        return key

    def report_success(self, service_type, key):
        self.successes.append((service_type, key))

    def report_error(self, service_type, key, reason):
        self.errors.append((service_type, key, reason))


def make_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")


@pytest.mark.asyncio
async def test_cloudflare_provider_writes_binary_artifact_without_leaking_key(tmp_path: Path):
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"png-fixture")

    keys = FakeKeys(["account-1:secret-token"])
    client = make_client(handler)
    provider = CloudflareImageProvider(keys, client=client, endpoint_base="https://api.test/accounts")
    path = await provider.generate(
        ImageGenerationRequest(prompt="istanbul skyline", width=512, height=512, max_output_bytes=1024)
    )
    try:
        assert Path(path).read_bytes() == b"png-fixture"
        assert requests[0].url.path.endswith("/accounts/account-1/ai/run/@cf/black-forest-labs/flux-2-klein-4b")
        assert requests[0].headers["authorization"] == "Bearer secret-token"
        assert keys.successes
    finally:
        Path(path).unlink(missing_ok=True)
        await client.aclose()


@pytest.mark.asyncio
async def test_cloudflare_provider_decodes_json_payload_and_rotates_after_quota():
    attempts = []
    encoded = base64.b64encode(b"json-image").decode("ascii")

    def handler(request: httpx.Request):
        attempts.append(request.headers["authorization"])
        if len(attempts) == 1:
            return httpx.Response(429, json={"error": "quota"})
        return httpx.Response(200, json={"success": True, "result": {"image": encoded}})

    keys = FakeKeys(["a:first", "b:second"])
    client = make_client(handler)
    provider = CloudflareImageProvider(keys, client=client, endpoint_base="https://api.test/accounts", max_attempts=2)
    path = await provider.generate(ImageGenerationRequest(prompt="test"))
    try:
        assert Path(path).read_bytes() == b"json-image"
        assert attempts == ["Bearer first", "Bearer second"]
        assert len(keys.errors) == 1
    finally:
        Path(path).unlink(missing_ok=True)
        await client.aclose()


@pytest.mark.asyncio
async def test_cloudflare_provider_policy_rejection_does_not_rotate():
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"success": False, "errors": [{"message": "NSFW content"}]})

    keys = FakeKeys(["a:first", "b:second"])
    client = make_client(handler)
    provider = CloudflareImageProvider(keys, client=client, endpoint_base="https://api.test/accounts", max_attempts=5)
    with pytest.raises(ImageProviderError) as error:
        await provider.generate(ImageGenerationRequest(prompt="unsafe"))
    assert error.value.code == "image_policy_rejected"
    assert calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_image_router_keeps_prompt_parser_outside_transport():
    captured = {}

    class Provider:
        async def generate(self, request):
            captured["request"] = request
            return "generated.png"

        async def health_check(self):
            return {"healthy": True}

        async def close(self):
            return None

    router = ImageRouter(None, provider=Provider())
    assert await router.generate_image("şehir --width 640 --height 480 --steps 30 --negative blur", max_output_bytes=2048) == "generated.png"
    request = captured["request"]
    assert request.prompt == "şehir"
    assert (request.width, request.height, request.num_steps) == (640, 480, 20)
    assert request.negative_prompt == "blur"
    assert request.max_output_bytes == 2048


@pytest.mark.asyncio
async def test_flux2_uses_multipart_without_changing_legacy_json_models():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, headers={'content-type':'image/png'}, content=b'fixture')

    async with make_client(handle) as client:
        provider = CloudflareImageProvider(FakeKeys(['account:token']), client=client)
        path = await provider.generate(ImageGenerationRequest(prompt='çaydanlık', negative_prompt='text'))
        Path(path).unlink()
        assert seen[0].headers['content-type'].startswith('multipart/form-data; boundary=')
        assert b'name="prompt"' in seen[0].content
        assert b'name="width"' in seen[0].content
        assert b'num_steps' not in seen[0].content
        assert b'Avoid: text' in seen[0].content
        path = await provider.generate(ImageGenerationRequest(prompt='test', model='@cf/black-forest-labs/flux-1-schnell'))
        Path(path).unlink()
        assert seen[1].headers['content-type'] == 'application/json'
        assert json.loads(seen[1].content)['num_steps'] == 4


@pytest.mark.asyncio
async def test_exhausted_image_attempt_reports_real_failure_not_missing_configuration():
    class Keys(FakeKeys):
        def get_key(self, service):
            if self.errors:
                raise ServiceCircuitOpenError(service, 30)
            return super().get_key(service)

    async with make_client(lambda _: httpx.Response(429, json={'error':'quota'})) as client:
        provider = CloudflareImageProvider(Keys(['account:token']), client=client, max_attempts=2)
        with pytest.raises(ImageProviderError) as error:
            await provider.generate(ImageGenerationRequest(prompt='test'))
        assert error.value.code == 'image_provider_quota'
