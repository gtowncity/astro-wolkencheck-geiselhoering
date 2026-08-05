"""Single-process async scheduler with per-source locks, retries and jitter."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    name: str
    interval_seconds: int
    timeout_seconds: int
    retry_attempts: int
    jitter_seconds: int
    runner: Callable[[], Awaitable[Any]]
    on_success: Callable[[Any], Awaitable[None]]
    on_error: Callable[[Exception], Awaitable[None]]


class RuntimeScheduler:
    def __init__(self, jobs: tuple[ScheduledJob, ...]) -> None:
        self._jobs = {job.name: job for job in jobs}
        self._locks = {job.name: asyncio.Lock() for job in jobs}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stopping = asyncio.Event()
        self._last_status: dict[str, str] = {job.name: "INITIALIZING" for job in jobs}

    @property
    def status(self) -> dict[str, str]:
        return dict(self._last_status)

    async def start(self) -> None:
        if self._tasks:
            return
        self._stopping.clear()
        for name in self._jobs:
            self._tasks[name] = asyncio.create_task(
                self._loop(self._jobs[name]), name=f"awc-{name}"
            )

    async def stop(self) -> None:
        self._stopping.set()
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def trigger(self, name: str | None = None) -> tuple[str, ...]:
        selected = tuple(self._jobs) if name is None else (name,)
        for item in selected:
            if item not in self._jobs:
                raise KeyError(item)
            asyncio.create_task(self.run_once(item), name=f"awc-manual-{item}")
        return selected

    async def run_once(self, name: str) -> bool:
        job = self._jobs[name]
        lock = self._locks[name]
        if lock.locked():
            return False
        async with lock:
            self._last_status[name] = "RUNNING"
            last_error: Exception | None = None
            for attempt in range(job.retry_attempts):
                try:
                    result = await asyncio.wait_for(
                        job.runner(), timeout=job.timeout_seconds
                    )
                    await job.on_success(result)
                    self._last_status[name] = "IDLE"
                    return True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = exc
                    if attempt + 1 < job.retry_attempts:
                        await asyncio.sleep(min(2 ** attempt, 10))
            assert last_error is not None
            await job.on_error(last_error)
            self._last_status[name] = "ERROR"
            return False

    async def _loop(self, job: ScheduledJob) -> None:
        try:
            while not self._stopping.is_set():
                await self.run_once(job.name)
                delay = float(job.interval_seconds)
                if job.jitter_seconds:
                    delay += random.uniform(0, job.jitter_seconds)
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            return
