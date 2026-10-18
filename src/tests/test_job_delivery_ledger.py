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
