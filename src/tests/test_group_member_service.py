from src.services.group_member_service import GroupMemberService


def test_group_member_service_renders_safe_welcome_and_goodbye_events():
    service = GroupMemberService()

    welcome = service.render(
        old_status="left",
        new_status="member",
        user_name="<Arel>",
        username="arel_bot",
        chat_title="Deneme & Grup",
        settings={"welcome_enabled": True},
    )
    assert welcome and "&lt;Arel&gt;" in welcome
    assert "Deneme &amp; Grup" in welcome
    assert "@arel_bot" in welcome

    goodbye = service.render(
        old_status="member",
        new_status="left",
        user_name="Arel",
        username=None,
        chat_title="Deneme",
        settings={"goodbye_enabled": True},
    )
    assert goodbye and "güle güle" in goodbye.casefold()


def test_group_member_service_ignores_disabled_and_non_transition_events():
    service = GroupMemberService()
    assert service.render(
        old_status="left",
        new_status="member",
        user_name="Arel",
        username=None,
        chat_title="Deneme",
        settings={},
    ) is None
    assert service.render(
        old_status="member",
        new_status="administrator",
        user_name="Arel",
        username=None,
        chat_title="Deneme",
        settings={"welcome_enabled": True},
    ) is None
