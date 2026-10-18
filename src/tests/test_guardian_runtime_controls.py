"""Guardian controls must affect consumers, not only acknowledge settings."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.moderation.guardian_surface import GuardianSurface
from src.moderation.telegram_guardian_service import GuardianTelegramService


class Port:
    def __init__(self):
        self.settings = {-100: {"guardian_federation": {"id": "arel-100", "created_by": 7}}}
        self.users = {}

    async def get_chat_settings(self, chat_id):
        return dict(self.settings.get(chat_id, {}))

    async def update_chat_setting_key(self, chat_id, key, value):
        self.settings.setdefault(chat_id, {})[key] = value

    async def get_user(self, user_id):
        return dict(self.users.get(user_id, {}))

    async def update_user(self, user_id, data):
        self.users.setdefault(user_id, {}).update(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("command,args", [
    ("fedapprove", ["42"]), ("fedunapprove", ["42"]),
    ("banlimit", ["2"]), ("promotelimit", ["2"]), ("grupinfolimit", ["2"]),
    ("privaterules", ["on"]), ("language", ["en"]),
    ("connect", []), ("disconnect", []), ("logchannel", ["-200"]),
    ("setappeallog", ["-200"]), ("setfedlog", ["-200"]),
    ("cleanservice", ["on"]), ("cleanwelcome", ["on"]), ("welcomemute", ["on"]),
])
async def test_previously_unwired_control_is_usable_and_permission_scoped(command, args):
    port = Port()
    surface = GuardianSurface(port)
    denied = await surface.execute(command, chat_id=-100, user_id=8, args=args, is_admin=False)
    assert denied is not None and not denied.changed
    result = await surface.execute(command, chat_id=-100, user_id=7, args=args, is_admin=True)
    assert result is not None and result.changed
    assert result.metadata.get("error_code") != "guardian_feature_unavailable"


@pytest.mark.asyncio
async def test_limit_blocks_next_action_and_is_actor_scoped():
    surface = GuardianSurface(Port())
    await surface.execute("banlimit", chat_id=-100, user_id=7, args=["1"], is_admin=True)
    assert await surface.consume_action_budget(-100, 7, "ban", "event-1")
    assert await surface.consume_action_budget(-100, 7, "ban", "event-1")  # retry
    assert not await surface.consume_action_budget(-100, 7, "ban", "event-2")
    assert await surface.consume_action_budget(-100, 8, "ban", "event-3")


@pytest.mark.asyncio
async def test_federation_trust_applies_in_joined_chat_without_bypassing_bans():
    from src.moderation.guardian_enforcement import evaluate_guardian_message
    port = Port()
    port.settings[-101] = {"guardian_federation": {"id": "arel-100"}, "locked_types": ["photo"]}
    surface = GuardianSurface(port)
    await surface.execute("fedapprove", chat_id=-100, user_id=7, args=["42"], is_admin=True)
    settings = await surface.effective_settings(-101)
    assert evaluate_guardian_message(attachment_type="photo", settings=settings, user={}, user_id=42).allowed
    assert not evaluate_guardian_message(attachment_type="photo", settings=settings, user={"guardian_gban": {"enabled": True}}, user_id=42).allowed
    await surface.execute("fedunapprove", chat_id=-100, user_id=7, args=["42"], is_admin=True)
    settings = await surface.effective_settings(-101)
    assert not evaluate_guardian_message(attachment_type="photo", settings=settings, user={}, user_id=42).allowed


@pytest.mark.asyncio
async def test_joined_chat_admin_cannot_modify_federation_owner_controls():
    port = Port()
    port.settings[-101] = {"guardian_federation": {"id": "arel-100"}}
    result = await GuardianSurface(port).execute("fedapprove", chat_id=-101, user_id=8, args=["42"], is_admin=True)
    assert not result.changed
    assert "approved_users" not in port.settings[-100]["guardian_federation"]


@pytest.mark.asyncio
async def test_service_cleanup_only_consumes_actual_service_updates():
    service = GuardianTelegramService()
    message = SimpleNamespace(new_chat_members=[SimpleNamespace(id=42)], left_chat_member=None, delete=AsyncMock())
    assert await service.cleanup_service_message(message=message, settings={"clean_service_enabled": True})
    message.delete.assert_awaited_once()
    normal = SimpleNamespace(new_chat_members=[], left_chat_member=None, delete=AsyncMock())
    assert not await service.cleanup_service_message(message=normal, settings={"clean_service_enabled": True})
    normal.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_welcome_cleanup_and_mute_have_real_transport_effects():
    port = Port()
    port.settings[-100].update(clean_welcome_enabled=True, welcome_mute_enabled=True, guardian_last_welcome_id=12)
    bot = SimpleNamespace(delete_message=AsyncMock(), restrict_chat_member=AsyncMock(), send_message=AsyncMock(return_value=SimpleNamespace(message_id=13)), get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))
    await GuardianTelegramService().deliver_welcome(bot=bot, data_port=port, chat_id=-100, user_id=42, text="Welcome", settings=port.settings[-100])
    bot.delete_message.assert_awaited_once_with(chat_id=-100, message_id=12)
    bot.restrict_chat_member.assert_awaited_once()
    assert port.settings[-100]["guardian_last_welcome_id"] == 13


@pytest.mark.asyncio
async def test_event_log_routes_only_bounded_metadata_to_configured_channel():
    bot = SimpleNamespace(send_message=AsyncMock())
    await GuardianTelegramService().emit_event(bot=bot, chat_id=-100, actor_id=7, action="ban", settings={"guardian_log_channel": -200}, target_user_id=42)
    assert bot.send_message.await_args.kwargs["chat_id"] == -200
    assert "ban" in bot.send_message.await_args.kwargs["text"]


def command_fixture(port, text, *, chat_id=-100, user_id=7):
    from src.handlers.command_handler import CommandHandler
    handler = CommandHandler.__new__(CommandHandler)
    handler.data_port = port
    handler.guardian_surface = GuardianSurface(port)
    handler.guardian_telegram_service = GuardianTelegramService()
    handler.announcement_service = None
    handler._is_group_admin = AsyncMock(return_value=True)
    handler.authorization = None
    message = SimpleNamespace(text=text, message_id=100, reply_to_message=None, reply_text=AsyncMock())
    bot = SimpleNamespace(send_message=AsyncMock(), get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")), set_chat_title=AsyncMock())
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id, type="supergroup" if chat_id < 0 else "private"), effective_user=SimpleNamespace(id=user_id), effective_message=message)
    return handler, update, SimpleNamespace(args=text.split()[1:], bot=bot)


@pytest.mark.asyncio
async def test_private_rules_command_never_posts_rules_in_public_group():
    port = Port()
    port.settings[-100].update(private_rules=True, guardian_rules="Private rules fixture")
    handler, update, context = command_fixture(port, "/rules")
    await handler.handle_guardian_command(update, context)
    context.bot.send_message.assert_awaited_once_with(chat_id=7, text="Private rules fixture")
    assert "Private rules fixture" not in update.effective_message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_group_language_reaches_menu_language_consumer():
    port = Port()
    await GuardianSurface(port).execute("language", chat_id=-100, user_id=7, args=["en"], is_admin=True)
    handler, update, _ = command_fixture(port, "/menu")
    assert await handler._get_language(update) == "en"


@pytest.mark.asyncio
async def test_connected_private_configuration_revalidates_current_group_admin():
    port = Port()
    handler, update, context = command_fixture(port, "/connect -100", chat_id=7)
    await handler.handle_guardian_command(update, context)
    assert port.users[7]["guardian_connected_chat"] == -100
    update.effective_message.text = "/cleanservice on"
    context.args = ["on"]
    await handler.handle_guardian_command(update, context)
    assert port.settings[-100]["clean_service_enabled"] is True
    context.bot.get_chat_member.return_value = SimpleNamespace(status="member")
    update.effective_message.text = "/cleanservice off"
    context.args = ["off"]
    await handler.handle_guardian_command(update, context)
    assert port.settings[-100]["clean_service_enabled"] is True
    update.effective_message.text = "/disconnect"
    context.args = []
    await handler.handle_guardian_command(update, context)
    assert port.users[7]["guardian_connected_chat"] is None


@pytest.mark.asyncio
async def test_group_info_limit_prevents_second_telegram_mutation():
    port = Port()
    port.settings[-100]["group_info_limit"] = 1
    handler, update, context = command_fixture(port, "/settitle First")
    await handler.handle_guardian_command(update, context)
    update.effective_message.message_id = 101
    context.args = ["Second"]
    await handler.handle_guardian_command(update, context)
    context.bot.set_chat_title.assert_awaited_once_with(chat_id=-100, title="First")


@pytest.mark.asyncio
async def test_member_transition_uses_welcome_mute_and_cleanup_controls():
    from src.handlers.message_handler import MessageHandler
    port = Port()
    port.settings[-100].update(welcome_enabled=True, welcome_mute_enabled=True, clean_welcome_enabled=True, guardian_last_welcome_id=12)
    handler = MessageHandler.__new__(MessageHandler)
    handler.data_port = port
    handler.guardian_telegram_service = GuardianTelegramService()
    user = SimpleNamespace(id=42, is_bot=False, first_name="Fixture", username=None)
    event = SimpleNamespace(chat=SimpleNamespace(id=-100, type="supergroup", title="Fixture"), new_chat_member=SimpleNamespace(user=user, status="member"), old_chat_member=SimpleNamespace(status="left"))
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=13)), restrict_chat_member=AsyncMock(), delete_message=AsyncMock(), get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))
    await handler.handle_chat_member_update(SimpleNamespace(chat_member=event), SimpleNamespace(bot=bot))
    bot.restrict_chat_member.assert_awaited_once()
    bot.delete_message.assert_awaited_once_with(chat_id=-100, message_id=12)


@pytest.mark.asyncio
async def test_appeal_report_reaches_configured_federation_log():
    port = Port()
    port.settings[-100]["guardian_federation"]["appeal_log_channel"] = -200
    port.add_audit_log = AsyncMock()
    handler, update, context = command_fixture(port, "/report itiraz")
    update.effective_message.reply_to_message = SimpleNamespace(from_user=SimpleNamespace(id=42), message_id=10)
    await handler.handle_guardian_command(update, context)
    assert context.bot.send_message.await_args.kwargs["chat_id"] == -200


@pytest.mark.asyncio
async def test_normal_report_uses_group_log_instead_of_appeal_log():
    port = Port()
    port.settings[-100]["guardian_federation"]["appeal_log_channel"] = -200
    port.settings[-100]["guardian_log_channel"] = -201
    port.add_audit_log = AsyncMock()
    handler, update, context = command_fixture(port, "/report")
    update.effective_message.reply_to_message = SimpleNamespace(from_user=SimpleNamespace(id=42), message_id=10)
    await handler.handle_guardian_command(update, context)
    assert context.bot.send_message.await_args.kwargs["chat_id"] == -201


@pytest.mark.asyncio
async def test_promotion_limit_covers_explicit_admin_permission_surface():
    port = Port()
    port.settings[-100]["promote_limit"] = 1
    surface = GuardianSurface(port)
    assert await surface.consume_action_budget(-100, 7, "admin_permissions", "one")
    assert not await surface.consume_action_budget(-100, 7, "promote", "two")


@pytest.mark.asyncio
@pytest.mark.parametrize("command,key,method", [("ban", "ban_limit", "ban_chat_member"), ("promote", "promote_limit", "promote_chat_member")])
async def test_manual_action_limit_blocks_second_real_transport_dispatch(command, key, method):
    port = Port()
    port.settings[-100][key] = 1
    handler, update, context = command_fixture(port, f"/{command}")
    update.effective_message.reply_to_message = SimpleNamespace(from_user=SimpleNamespace(id=42), message_id=10)
    context.bot.get_chat_member.side_effect = lambda **kwargs: SimpleNamespace(status="administrator" if kwargs["user_id"] == 7 else "member")
    setattr(context.bot, method, AsyncMock())
    await handler.handle_guardian_command(update, context)
    update.effective_message.message_id = 101
    await handler.handle_guardian_command(update, context)
    assert getattr(context.bot, method).await_count == 1


@pytest.mark.asyncio
async def test_log_channel_rejects_positive_user_destination():
    port = Port()
    result = await GuardianSurface(port).execute("logchannel", chat_id=-100, user_id=7, args=["42"], is_admin=True)
    assert not result.changed
    assert "guardian_log_channel" not in port.settings[-100]


@pytest.mark.asyncio
async def test_federation_transfer_revokes_previous_owner_trust_management():
    port = Port()
    surface = GuardianSurface(port)
    result = await surface.execute("fedtransfer", chat_id=-100, user_id=7, args=["8"], is_admin=True)
    assert result.changed
    denied = await surface.execute("fedapprove", chat_id=-100, user_id=7, args=["42"], is_admin=True)
    assert not denied.changed
    allowed = await surface.execute("fedapprove", chat_id=-100, user_id=8, args=["42"], is_admin=True)
    assert allowed.changed


@pytest.mark.asyncio
async def test_limits_survive_service_recreation_and_expire_after_one_hour(monkeypatch):
    port = Port()
    port.settings[-100]["ban_limit"] = 1
    monkeypatch.setattr("src.moderation.guardian_surface.time.time", lambda: 10000.0)
    assert await GuardianSurface(port).consume_action_budget(-100, 7, "ban", "first")
    assert not await GuardianSurface(port).consume_action_budget(-100, 7, "ban", "second")
    monkeypatch.setattr("src.moderation.guardian_surface.time.time", lambda: 13601.0)
    assert await GuardianSurface(port).consume_action_budget(-100, 7, "ban", "third")


@pytest.mark.asyncio
async def test_private_rules_delivery_failure_does_not_disclose_rules_to_group():
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("fixture delivery failure")))
    message = SimpleNamespace(reply_text=AsyncMock())
    delivered = await GuardianTelegramService().deliver_rules(bot=bot, message=message, chat_id=-100, user_id=7,
        settings={"private_rules": True, "guardian_rules": "Sensitive fixture rules"})
    assert not delivered
    assert "Sensitive fixture rules" not in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_welcome_mute_never_restricts_an_administrator():
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")), restrict_chat_member=AsyncMock())
    await GuardianTelegramService().deliver_welcome(bot=bot, data_port=Port(), chat_id=-100, user_id=7,
        text=None, settings={"welcome_mute_enabled": True})
    bot.restrict_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_lookup_outage_does_not_turn_completed_action_into_failure():
    port = SimpleNamespace(get_chat_settings=AsyncMock(side_effect=RuntimeError("fixture store outage")))
    bot = SimpleNamespace(send_message=AsyncMock())
    await GuardianTelegramService().emit_event(bot=bot, data_port=port, chat_id=-100, actor_id=7, action="ban")
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_connected_commands_respect_group_disabled_command_policy():
    port = Port()
    port.users[7] = {"guardian_connected_chat": -100}
    port.settings[-100]["disabled_commands"] = ["cleanservice"]
    handler, update, context = command_fixture(port, "/cleanservice on", chat_id=7)
    await handler.handle_guardian_command(update, context)
    assert "clean_service_enabled" not in port.settings[-100]


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["cleanservice", "cleanwelcome", "welcomemute"])
async def test_invalid_control_argument_never_flips_security_setting(command):
    port = Port()
    result = await GuardianSurface(port).execute(command, chat_id=-100, user_id=7, args=["fixture-invalid"], is_admin=True)
    assert not result.changed
