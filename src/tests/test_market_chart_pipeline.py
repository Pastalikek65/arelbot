import pytest

from src.market_chart.job_queue import MarketChartJobQueue
from src.market_chart.service import MarketChartProcessingService


class Store:
    def __init__(self): self.rows = {}
    async def create(self, row): self.rows[row["job_id"]] = dict(row)
    async def update(self, job_id, **changes): self.rows.setdefault(job_id, {}).update(changes)
    async def get(self, job_id): return self.rows.get(job_id)
    async def delete(self, job_id): self.rows.pop(job_id, None)


@pytest.mark.asyncio
async def test_market_chart_submit_bounds_and_deduplicates():
    service = MarketChartProcessingService(renderer=object(), database=None)
    service.queue = MarketChartJobQueue(store=Store())
    data = {"symbol": "THYAO", "rows": [{"timestamp": i, "close": i + 1} for i in range(3)]}
    first, _ = await service.submit(
        chat_id=1,
        user_id=2,
        status_message_id=3,
        data=data,
        caption="fiyat",
        message_thread_id=77,
    )
    second, duplicate = await service.submit(chat_id=1, user_id=2, status_message_id=3, data=data, caption="fiyat")
    assert first == second
    assert duplicate
    assert service.queue.local_queue.qsize() == 1
    assert service.queue.store.rows[first]["message_thread_id"] == 77
