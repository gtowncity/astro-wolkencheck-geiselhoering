import asyncio

import pytest

from nowcast_service.runtime.scheduler import RuntimeScheduler, ScheduledJob


@pytest.mark.asyncio
async def test_scheduler_retries_prevents_overlap_and_reports_error() -> None:
    calls = 0

    async def runner() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary")
        return "ok"

    values: list[str] = []

    async def success(value: str) -> None:
        values.append(value)

    async def error(exc: Exception) -> None:
        values.append(type(exc).__name__)

    scheduler = RuntimeScheduler(
        (ScheduledJob("job", 60, 1, 2, 0, runner, success, error),)
    )
    assert scheduler.status == {"job": "INITIALIZING"}
    assert await scheduler.run_once("job") is True
    assert scheduler.status == {"job": "IDLE"}
    assert values == ["ok"]
    assert await scheduler.trigger("job") == ("job",)
    with pytest.raises(KeyError):
        await scheduler.trigger("missing")
    await asyncio.sleep(0)
    await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_rejects_overlap_until_running_job_finishes() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    values: list[str] = []

    async def runner() -> str:
        started.set()
        await release.wait()
        return "finished"

    async def success(value: str) -> None:
        values.append(value)

    async def error(exc: Exception) -> None:
        pytest.fail(f"unexpected scheduler error: {exc}")

    scheduler = RuntimeScheduler(
        (ScheduledJob("job", 60, 1, 1, 0, runner, success, error),)
    )
    first = asyncio.create_task(scheduler.run_once("job"))
    await asyncio.wait_for(started.wait(), timeout=1)
    assert scheduler.status == {"job": "RUNNING"}
    assert await scheduler.run_once("job") is False
    release.set()
    assert await first is True
    assert values == ["finished"]
    assert scheduler.status == {"job": "IDLE"}


@pytest.mark.asyncio
async def test_scheduler_exhausts_retries_and_reports_last_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    delays: list[float] = []
    errors: list[Exception] = []

    async def runner() -> str:
        nonlocal attempts
        attempts += 1
        raise ValueError(f"failure-{attempts}")

    async def success(_: str) -> None:
        pytest.fail("failed runner must not call success")

    async def error(exc: Exception) -> None:
        errors.append(exc)

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    scheduler = RuntimeScheduler(
        (ScheduledJob("job", 60, 1, 3, 0, runner, success, error),)
    )

    assert await scheduler.run_once("job") is False
    assert attempts == 3
    assert delays == [1, 2]
    assert len(errors) == 1
    assert str(errors[0]) == "failure-3"
    assert scheduler.status == {"job": "ERROR"}


@pytest.mark.asyncio
async def test_scheduler_start_stop_all_jobs_and_cancel_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = {"a": asyncio.Event(), "b": asyncio.Event()}
    calls = {"a": 0, "b": 0}

    def job(name: str) -> ScheduledJob:
        async def runner() -> str:
            calls[name] += 1
            return name

        async def success(value: str) -> None:
            assert value == name
            completed[name].set()

        async def error(exc: Exception) -> None:
            pytest.fail(f"unexpected scheduler error: {exc}")

        return ScheduledJob(name, 600, 1, 1, 5, runner, success, error)

    monkeypatch.setattr("nowcast_service.runtime.scheduler.random.uniform", lambda _a, _b: 0.0)
    scheduler = RuntimeScheduler((job("a"), job("b")))

    await scheduler.start()
    await scheduler.start()
    await asyncio.wait_for(completed["a"].wait(), timeout=1)
    await asyncio.wait_for(completed["b"].wait(), timeout=1)
    assert calls == {"a": 1, "b": 1}
    assert scheduler.status == {"a": "IDLE", "b": "IDLE"}

    assert await scheduler.trigger() == ("a", "b")
    await asyncio.sleep(0)
    await scheduler.stop()
    await scheduler.stop()
