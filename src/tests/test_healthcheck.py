from scripts.healthcheck import check_binary, check_encryption_key, check_mongo, check_redis
from src.database import _database_name_from_url


def test_healthcheck_rejects_unreachable_dependencies_without_leaking_uri():
    assert check_mongo("mongodb://127.0.0.1:1/arel") is False
    assert check_redis("redis://127.0.0.1:1/0") is False


def test_database_name_is_taken_from_mongodb_uri_path():
    assert _database_name_from_url("mongodb://localhost:27017/arel_bot_test") == "arel_bot_test"
    assert _database_name_from_url(
        "mongodb://user:secret@localhost:27017/arel_bot_test?authSource=admin"
    ) == "arel_bot_test"
    assert _database_name_from_url("mongodb://localhost:27017") == "arel_bot"


def test_runtime_binary_probe_is_boolean_and_safe(monkeypatch):
    monkeypatch.setattr("scripts.healthcheck.shutil.which", lambda name: "/usr/bin/" + name)
    assert check_binary("ffmpeg") is True

    monkeypatch.setattr("scripts.healthcheck.shutil.which", lambda name: None)
    assert check_binary("ffprobe") is False


def test_encryption_probe_requires_length_only_when_cookie_vault_is_enabled():
    assert check_encryption_key(None, required=False) is True
    assert check_encryption_key("short", required=False) is True
    assert check_encryption_key("short", required=True) is False
    assert check_encryption_key("x" * 32, required=True) is True
