from types import SimpleNamespace

from src.handlers.command_handler import CommandHandler
from src.moderation.guardian_commands import GuardianCommandService
from src.moderation.telegram_guardian_service import GuardianTelegramService


class _ResolverBot:
    async def get_chat_member(self, **kwargs):
        return SimpleNamespace(status="member")


class _ResolverPort:
    async def find_user_by_username(self, username):
        return {"_id": 123, "username": username}


def test_guardian_aliases_normalize_to_one_action_vocabulary():
    assert GuardianCommandService.canonical("/sustur") == "mute"
    assert GuardianCommandService.canonical("/mute@arelbot") == "mute"
    assert GuardianCommandService.canonical("/yasakla") == "ban"
    assert GuardianCommandService.canonical("/agkur") == "newfed"


def test_arel_guardian_aliases_resolve_to_implemented_commands():
    expected = {
        "yetkiver": "admin",
        "yetkial": "deadmin",
        "hayalet": "hayaletban",
        "rapor": "report",
        "linkkoruma": "antilink",
        "reklamkoruma": "antibotlink",
        "raidkoruma": "antiraid",
        "dogrulama": "captcha",
        "hosgeldin": "welcome",
        "veda": "goodbye",
        "gecemodu": "nightmode",
        "grupdili": "language",
        "filtre": "filter",
        "filtreler": "filters",
        "filtresil": "stop",
        "risk": "whois",
        "guvenliliste": "fedapproved",
    }
    assert {name: GuardianCommandService.canonical(name) for name in expected} == expected


def test_guardian_action_contains_exact_target_and_thread_identity():
    action = GuardianCommandService.build_action(
        "mute",
        chat_id=-100,
        actor_id=900,
        target_user_id=42,
        thread_id=7,
        generation_id="guardian:-100:55",
    )
    assert action.action == "mute"
    assert action.target_user_id == 42
    assert action.thread_id == 7
    assert action.metadata["source"] == "kek_guardian_compat"


def test_temporary_mute_duration_is_bounded_and_typed():
    action = GuardianCommandService.build_action(
        "tmute",
        chat_id=-100,
        actor_id=900,
        target_user_id=42,
        duration="2h",
    )
    assert action.action == "temp_mute"
    assert action.duration_seconds == 7200


def test_temporary_ban_uses_typed_expiry_instead_of_permanent_ban():
    action = GuardianCommandService.build_action(
        "tban",
        chat_id=-100,
        actor_id=900,
        target_user_id=42,
        duration="15m",
    )
    assert action.action == "temp_ban"
    assert action.duration_seconds == 900


def test_guardian_role_commands_are_typed_actions():
    promote = GuardianCommandService.build_action(
        "promote",
        chat_id=-100,
        actor_id=7,
        target_user_id=42,
        generation_id="guardian:role:1",
    )
    demote = GuardianCommandService.build_action(
        "demote",
        chat_id=-100,
        actor_id=7,
        target_user_id=42,
        generation_id="guardian:role:2",
    )
    assert promote.action == "promote"
    assert demote.action == "demote"


def test_command_handler_resolves_guardian_target_from_argument_without_reply():
    update = SimpleNamespace(
        effective_message=SimpleNamespace(reply_to_message=None),
    )
    context = SimpleNamespace(args=["123"])

    assert CommandHandler._moderation_target_id(update, context) == 123


def test_command_handler_keeps_numeric_target_compatibility_for_temporary_actions():
    # The async username resolver is exercised in the transport tests; the
    # static compatibility helper remains intentionally numeric-only.
    update = SimpleNamespace(effective_message=SimpleNamespace(reply_to_message=None))
    context = SimpleNamespace(args=["123"])
    assert CommandHandler._moderation_target_id(update, context) == 123


import pytest


@pytest.mark.asyncio
async def test_command_handler_resolves_username_argument_for_guardian_action():
    handler = CommandHandler.__new__(CommandHandler)
    handler.guardian_telegram_service = GuardianTelegramService()
    handler.data_port = _ResolverPort()
    update = SimpleNamespace(
        effective_message=SimpleNamespace(reply_to_message=None),
        effective_chat=SimpleNamespace(id=-100),
    )
    context = SimpleNamespace(args=["@hedef"], bot=_ResolverBot())
    assert await handler._resolve_moderation_target_id(update, context, command="ban") == 123


@pytest.mark.asyncio
async def test_command_handler_prefers_telegram_text_mention_identity():
    handler = CommandHandler.__new__(CommandHandler)
    handler.guardian_telegram_service = GuardianTelegramService()
    handler.data_port = _ResolverPort()
    update = SimpleNamespace(
        effective_message=SimpleNamespace(
            reply_to_message=None,
            entities=[SimpleNamespace(type="text_mention", user=SimpleNamespace(id=321))],
        ),
        effective_chat=SimpleNamespace(id=-100),
    )
    context = SimpleNamespace(args=[], bot=_ResolverBot())
    assert await handler._resolve_moderation_target_id(update, context, command="ban") == 321
