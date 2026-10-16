from datetime import datetime, timezone

from src.services.time_format import turkey_now


def test_turkey_now_converts_utc_independently_of_host_timezone():
    source = datetime(2026, 10, 5, 21, 30, tzinfo=timezone.utc)

    local = turkey_now(source)

    assert local.isoformat() == "2026-10-06T00:30:00+03:00"
    assert local.tzinfo is not None


def test_turkey_now_treats_naive_values_as_utc():
    local = turkey_now(datetime(2026, 10, 5, 21, 30))

    assert local.isoformat() == "2026-10-06T00:30:00+03:00"
