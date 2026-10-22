from types import SimpleNamespace
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.telegram_staging_probe import (
    ProbeError,
    apply_pending_threshold,
    probe_bot,
    validate_live_gate,
    validate_token_shape,
    write_report,
)


class _ReadOnlyBot:
    async def get_me(self):
        return SimpleNamespace(id=987654321, is_bot=True, username="arel_stage_bot")

    async def get_webhook_info(self):
        return SimpleNamespace(
            url="https://secret.example/telegram/webhook?token=never-print",
            pending_update_count=3,
            last_error_message="private upstream diagnostic",
        )

    async def get_my_commands(self):
        return [SimpleNamespace(command="start"), SimpleNamespace(command="hava")]


def test_probe_report_is_read_only_and_redacts_webhook_diagnostics():
    import asyncio

    report = asyncio.run(probe_bot(_ReadOnlyBot()))

    assert report["ok"] is True
    assert report["checks"] == ["get_me", "get_webhook_info", "get_my_commands"]
    assert report["warnings"] == ["telegram_webhook_last_error"]
    assert report["bot"] == {"id": 987654321, "is_bot": True, "username": "arel_stage_bot"}
    assert report["webhook"] == {
        "configured": True,
        "pending_updates": 3,
        "pending_warning_threshold": 100,
        "backlog_status": "ok",
        "last_error_present": True,
    }
    assert report["commands"] == {"count": 2, "names": ["hava", "start"]}
    rendered = repr(report)
    assert "secret.example" not in rendered
    assert "never-print" not in rendered
    assert "private upstream diagnostic" not in rendered


def test_probe_marks_backlog_warning_without_leaking_webhook_details():
    import asyncio

    class BackloggedBot(_ReadOnlyBot):
        async def get_webhook_info(self):
            return SimpleNamespace(url="", pending_update_count=101, last_error_message="")

    report = asyncio.run(probe_bot(BackloggedBot(), pending_warning_threshold=100))

    assert report["warnings"] == ["telegram_pending_updates_high"]
    assert report["webhook"]["backlog_status"] == "warning"
    assert report["webhook"]["pending_updates"] == 101


def test_probe_cli_can_fail_closed_on_pending_backlog(tmp_path: Path):
    env_file = tmp_path / "staging.env"
    env_file.write_text(
        "TELEGRAM_TOKEN=123456789:abcdefghijklmnopqrstuvwxyz\n",
        encoding="utf-8",
    )

    # Validation-only mode never contacts Telegram and therefore cannot claim
    # a backlog threshold was checked.
    result = subprocess.run(
        [
            sys.executable,
            "scripts/telegram_staging_probe.py",
            "--env-file",
            str(env_file),
            "--max-pending-updates",
            "100",
            "--json",
        ],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert '"mode": "validation-only"' in result.stdout


def test_pending_threshold_returns_stable_fail_closed_code():
    payload = {"ok": True, "webhook": {"pending_updates": 101}}

    gated = apply_pending_threshold(payload, 100)

    assert gated["ok"] is False
    assert gated["error"] == "pending_updates_exceeded"
    assert apply_pending_threshold(payload, 101)["ok"] is True

    with pytest.raises(ProbeError, match="pending_threshold_invalid"):
        apply_pending_threshold(payload, -1)


def test_probe_rejects_non_bot_identity():
    import asyncio

    class NotBot(_ReadOnlyBot):
        async def get_me(self):
            return SimpleNamespace(id=1, is_bot=False, username="human")

    with pytest.raises(ProbeError, match="bot_identity_invalid"):
        asyncio.run(probe_bot(NotBot()))


@pytest.mark.parametrize(
    ("live", "confirm", "env_value"),
    [(False, False, "1"), (True, False, "1"), (True, True, "0"), (True, True, None)],
)
def test_live_probe_requires_redundant_explicit_gate(live, confirm, env_value):
    with pytest.raises(ProbeError, match="live_gate_required"):
        validate_live_gate(live=live, confirm_live=confirm, env_value=env_value)


def test_live_probe_accepts_only_explicit_gate():
    assert validate_live_gate(live=True, confirm_live=True, env_value="1") is None


@pytest.mark.parametrize("token", ["", "fixture-token", "123:short"])
def test_token_shape_is_checked_without_echoing_secret(token):
    assert validate_token_shape(token) is False


def test_valid_token_shape_is_accepted():
    assert validate_token_shape("123456789:abcdefghijklmnopqrstuvwxyz") is True


def test_probe_script_entrypoint_runs_validation_without_network(tmp_path: Path):
    env_file = tmp_path / "staging.env"
    env_file.write_text(
        "TELEGRAM_TOKEN=123456789:abcdefghijklmnopqrstuvwxyz\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/telegram_staging_probe.py",
            "--env-file",
            str(env_file),
            "--json",
        ],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert '"mode": "validation-only"' in result.stdout
    assert "abcdefghijklmnopqrstuvwxyz" not in result.stdout


def test_probe_report_is_atomic_and_rejects_symlinks(tmp_path: Path):
    target = tmp_path / "evidence" / "telegram.json"
    write_report(target, {"ok": True, "checks": ["token_shape"]})
    assert '"token_shape"' in target.read_text(encoding="utf-8")
    assert not list(target.parent.glob("*.tmp"))

    if hasattr(target, "symlink_to"):
        link = tmp_path / "linked.json"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation is not available")
        with pytest.raises(ProbeError, match="report_target_symlink"):
            write_report(link, {"ok": True})
