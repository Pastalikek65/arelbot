from __future__ import annotations

from types import SimpleNamespace

from main import run_transport


class FakeApplication:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def run_polling(self, **kwargs):
        self.calls.append(("polling", kwargs))
        return "polling-started"

    def run_webhook(self, **kwargs):
        self.calls.append(("webhook", kwargs))
        return "webhook-started"


def test_windows_polling_dispatch_uses_only_polling_options():
    application = FakeApplication()
    transport = SimpleNamespace(
        mode="polling",
        polling_kwargs=lambda: {"allowed_updates": ["message"]},
        webhook_kwargs=lambda: (_ for _ in ()).throw(AssertionError("webhook path must not run")),
    )

    assert run_transport(application, transport) == "polling-started"
    assert application.calls == [("polling", {"allowed_updates": ["message"]})]


def test_linux_webhook_dispatch_uses_only_webhook_options():
    application = FakeApplication()
    transport = SimpleNamespace(
        mode="webhook",
        webhook_kwargs=lambda: {
            "listen": "127.0.0.1",
            "port": 8443,
            "url_path": "telegram/webhook",
            "secret_token": "s" * 32,
        },
        polling_kwargs=lambda: (_ for _ in ()).throw(AssertionError("polling path must not run")),
    )

    assert run_transport(application, transport) == "webhook-started"
    assert application.calls == [
        (
            "webhook",
            {
                "listen": "127.0.0.1",
                "port": 8443,
                "url_path": "telegram/webhook",
                "secret_token": "s" * 32,
            },
        )
    ]


def test_unknown_transport_mode_fails_closed_before_starting_telegram():
    application = FakeApplication()
    transport = SimpleNamespace(mode="unknown")

    try:
        run_transport(application, transport)
    except ValueError as exc:
        assert str(exc) == "Unsupported Telegram transport mode"
    else:
        raise AssertionError("unknown transport mode must be rejected")
    assert application.calls == []
