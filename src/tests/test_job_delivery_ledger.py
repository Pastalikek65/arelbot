import pytest

from src.jobs.delivery_ledger import JobDeliveryLedger


@pytest.mark.asyncio
async def test_delivery_ledger_claim_send_and_duplicate_protection():
    ledger = JobDeliveryLedger()
    assert await ledger.claim("job:1", job_id="job-1", chat_id=10)
    assert await ledger.begin_send("job:1")
    assert await ledger.mark_sent("job:1", 99)
    assert await ledger.claim("job:1", job_id="job-1", chat_id=10) is False


@pytest.mark.asyncio
async def test_delivery_ledger_marks_crashed_send_uncertain():
    ledger = JobDeliveryLedger()
    assert await ledger.claim("job:2", job_id="job-2", chat_id=10)
    assert await ledger.begin_send("job:2")
    await ledger.mark_uncertain("job:2", "worker_crash")
    # A second worker cannot replay a send whose outcome is unknown.
    assert await ledger.claim("job:2", job_id="job-2", chat_id=10) is False


@pytest.mark.asyncio
async def test_delivery_ledger_releases_a_refused_send_and_allows_a_new_claim():
    ledger = JobDeliveryLedger()
    assert await ledger.claim("job:3", job_id="job-3", chat_id=10)
    assert await ledger.begin_send("job:3")
    assert await ledger.delivery_status("job:3") == "sending"

    await ledger.release_started("job:3", "delivery_refused")

    # A definitively refused delivery is claimable again, and the row no
    # longer advertises a send that never happened.
    assert await ledger.delivery_status("job:3") == "failed"
    assert ledger._items["job:3"]["send_started_at"] is None
    assert await ledger.claim("job:3", job_id="job-3", chat_id=10)
    assert await ledger.begin_send("job:3")
    assert await ledger.mark_sent("job:3", 7)
    assert await ledger.delivery_status("job:3") == "sent"


@pytest.mark.asyncio
async def test_delivery_ledger_never_reopens_a_sent_delivery():
    ledger = JobDeliveryLedger()
    assert await ledger.claim("job:4", job_id="job-4", chat_id=10)
    assert await ledger.begin_send("job:4")
    assert await ledger.mark_sent("job:4", 8)

    await ledger.release_started("job:4", "delivery_refused")

    # A sent delivery stays terminal and is never replayed.
    assert await ledger.delivery_status("job:4") == "sent"
    assert await ledger.claim("job:4", job_id="job-4", chat_id=10) is False


@pytest.mark.asyncio
async def test_delivery_ledger_reports_an_unknown_delivery():
    ledger = JobDeliveryLedger()
    assert await ledger.delivery_status("job:missing") is None
