import asyncio
import pytest
from nowcast_service.runtime.scheduler import RuntimeScheduler, ScheduledJob


@pytest.mark.asyncio
async def test_scheduler_retries_prevents_overlap_and_reports_error() -> None:
    calls = 0
    async def runner() -> str:
        nonlocal calls
        calls += 1
        if calls == 1: raise RuntimeError("temporary")
        return "ok"
    values = []
    async def success(value: str) -> None: values.append(value)
    async def error(exc: Exception) -> None: values.append(type(exc).__name__)
    scheduler = RuntimeScheduler((ScheduledJob("job", 60, 1, 2, 0, runner, success, error),))
    assert await scheduler.run_once("job") is True
    assert values == ["ok"]
    assert await scheduler.trigger("job") == ("job",)
    with pytest.raises(KeyError): await scheduler.trigger("missing")
    await asyncio.sleep(0)
    await scheduler.stop()
