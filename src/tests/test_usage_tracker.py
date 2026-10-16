import pytest

from src.ai.usage_tracker import UsageTracker


@pytest.mark.asyncio
async def test_usage_tracker_calculates_cost_and_persists_only_telemetry():
    class Database:
        available = True

        def __init__(self):
            self.records = []

        async def record_ai_usage(self, document):
            self.records.append(document)

    database = Database()
    tracker = UsageTracker(database, {"groq": {"input": 0.2, "output": 0.8}})
    await tracker.record(
        "groq", success=True, latency_ms=42, input_tokens=1000,
        output_tokens=500, task="summary",
    )
    snapshot = await tracker.snapshot()
    assert snapshot["groq"]["requests"] == 1
    assert snapshot["groq"]["cost_usd"] == pytest.approx(0.0006)
    assert database.records[0]["task"] == "summary"
    assert "prompt" not in database.records[0]
    assert "response" not in database.records[0]


@pytest.mark.asyncio
async def test_usage_tracker_does_not_break_when_persistence_fails():
    class Database:
        available = True

        async def record_ai_usage(self, document):
            raise RuntimeError("database unavailable")

    tracker = UsageTracker(Database())
    await tracker.record("provider", success=False, latency_ms=-5)
    snapshot = await tracker.snapshot()
    assert snapshot["provider"]["failures"] == 1
    assert snapshot["provider"]["total_latency_ms"] == 0

