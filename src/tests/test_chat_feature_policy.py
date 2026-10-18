import pytest

from src.features.chat_feature_policy import ChatFeaturePolicy


class Port:
    def __init__(self):
        self.settings = {10: {"q_enabled": False}, 20: {"media_enabled": False}}
        self.audit = []

    async def get_chat_settings(self, chat_id):
        return dict(self.settings.get(chat_id, {}))

    async def update_chat_setting_key(self, chat_id, key, value):
        self.settings.setdefault(chat_id, {})[key] = value

    async def add_audit_log(self, actor_id, action, **details):
        self.audit.append((actor_id, action, details))


@pytest.mark.asyncio
async def test_policy_is_scoped_per_chat_and_normalizes_aliases():
    policy = ChatFeaturePolicy(Port())
    assert await policy.is_enabled(10, "quote") is False
    assert await policy.is_enabled(20, "media") is False
    assert await policy.is_enabled(10, "media") is True
    assert await policy.is_enabled(20, "quotes") is True


@pytest.mark.asyncio
async def test_policy_set_records_bounded_audit_without_cross_chat_mutation():
    port = Port()
    policy = ChatFeaturePolicy(port)
    await policy.set_enabled(10, "media", False, actor_id=99)
    assert port.settings[10]["media_enabled"] is False
    assert port.settings[20]["media_enabled"] is False
    assert port.audit == [(99, "chat_feature_toggle", {"feature": "media_enabled", "enabled": False})]


@pytest.mark.asyncio
async def test_policy_uses_default_when_repository_is_unavailable():
    class Broken:
        async def get_chat_settings(self, chat_id):
            raise RuntimeError("unavailable")

    assert await ChatFeaturePolicy(Broken()).is_enabled(10, "quote") is True
    assert await ChatFeaturePolicy(None).is_enabled(10, "media", default=False) is False
