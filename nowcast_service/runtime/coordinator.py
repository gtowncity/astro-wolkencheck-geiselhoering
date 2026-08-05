"""Atomic runtime coordination for source snapshots, latches and alarms."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from nowcast_service.alerts.engine import AlertEngine
from nowcast_service.config import AppConfig
from nowcast_service.decision_engine import EquipmentState, SourceHealth, SourceState, evaluate_safety
from nowcast_service.hazard_latch import HazardLatchRegistry, HazardSignal
from nowcast_service.persistence.database import RuntimeDatabase
from nowcast_service.runtime.event_bus import EventBus
from nowcast_service.runtime.models import DecisionSnapshot, SourceSnapshot, iso, utc_now
from nowcast_service.runtime.scheduler import RuntimeScheduler, ScheduledJob
from nowcast_service.sources.runtime_base import SourceRunError, SourceRunner

ALGORITHM_VERSION = "safety-1.0.0"


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else None


class RuntimeCoordinator:
    def __init__(self, *, config: AppConfig, data_dir: Path, source_runners: tuple[SourceRunner, ...] = ()) -> None:
        self._lock = RLock()
        self._config = config
        self._database = RuntimeDatabase(data_dir)
        self.events = EventBus()
        self._persistence_ok = True
        try:
            self._database.initialize()
            restored = self._database.load_active_hazards()
        except RuntimeError:
            restored = ()
            self._persistence_ok = False
        self._latches = HazardLatchRegistry(restored)
        self._alerts = AlertEngine(
            red_repeat_seconds=config.alerts.red_repeat_seconds,
            yellow_repeat_seconds=config.alerts.yellow_repeat_seconds,
        )
        self._sources = self._initial_sources()
        self._last_good: dict[str, SourceSnapshot] = {}
        self._current: DecisionSnapshot | None = None
        self._started_at = utc_now()
        self._runners = {item.source_id: item for item in source_runners}
        self._scheduler = self._make_scheduler()
        self._evaluate(utc_now(), persist=False)

    def _initial_sources(self) -> dict[str, SourceSnapshot]:
        now = utc_now()
        def make(source: str, state: SourceState, stale: int, invalid: int, complete: bool = False) -> SourceSnapshot:
            return SourceSnapshot(source, f"{source}:initial", source, None, None, None, None, None,
                now, state, None, complete, stale, invalid,
                failure_message=None if complete else "Source has not completed a trusted cycle.")
        return {
            "DWD_RV": make("DWD_RV", SourceState.INITIALIZING,
                self._config.thresholds.radar_stale_after_minutes * 60,
                self._config.thresholds.radar_invalid_after_minutes * 60),
            "DWD_CAP": make("DWD_CAP", SourceState.INITIALIZING,
                self._config.thresholds.cap_stale_after_minutes * 60,
                self._config.thresholds.cap_invalid_after_minutes * 60),
            "DWD_WN": make("DWD_WN", SourceState.NOT_AVAILABLE, 900, 1800),
            "RAIN_SENSOR": make("RAIN_SENSOR", SourceState.DISABLED, 60, 120),
            "LOCAL_PERSISTENCE": make("LOCAL_PERSISTENCE",
                SourceState.LIVE if self._persistence_ok else SourceState.FAILED,
                86400, 172800, self._persistence_ok),
        }

    def _make_scheduler(self) -> RuntimeScheduler:
        jobs: list[ScheduledJob] = []
        for source_id, runner in self._runners.items():
            interval = (self._config.schedule.radar_interval_seconds
                if source_id == "DWD_RV" else self._config.schedule.cap_interval_seconds)
            async def run(current: SourceRunner = runner) -> SourceSnapshot:
                return await current.run(evaluated_at=utc_now())
            async def success(snapshot: SourceSnapshot, expected: str = source_id) -> None:
                if snapshot.source_id != expected:
                    raise RuntimeError("Source runner returned a mismatched source ID")
                await self.ingest(snapshot)
            async def error(exc: Exception, expected: str = source_id) -> None:
                await self.mark_source_error(expected, exc)
            jobs.append(ScheduledJob(source_id, interval, self._config.schedule.timeout_seconds,
                self._config.schedule.retry_attempts, self._config.schedule.jitter_seconds,
                run, success, error))
        async def maintenance() -> None:
            await self.maintenance()
        async def ignore(_: object) -> None:
            return None
        jobs.append(ScheduledJob("maintenance", self._config.schedule.maintenance_interval_seconds,
            30, 1, 0, maintenance, ignore, ignore))
        return RuntimeScheduler(tuple(jobs))

    async def start(self) -> None:
        await self.events.publish("runtime-status", self.runtime_dict())
        await self.events.publish("snapshot", self.snapshot_dict())
        await self._scheduler.start()

    async def stop(self) -> None:
        await self._scheduler.stop()

    @property
    def equipment_state(self) -> EquipmentState:
        return self._config.equipment_state

    def source_health(self, *, now: datetime | None = None) -> tuple[SourceHealth, ...]:
        current = now or utc_now()
        with self._lock:
            result = []
            for source, snapshot in self._sources.items():
                state = snapshot.effective_state(current)
                detail = snapshot.failure_message
                if snapshot.state is SourceState.LIVE and state is not SourceState.LIVE:
                    detail = ("Source data exceeded its invalid limit." if state is SourceState.FAILED else "Source data exceeded its stale limit.")
                result.append(SourceHealth(source, source in {"DWD_RV", "DWD_CAP", "LOCAL_PERSISTENCE"},
                    state, source in {"DWD_WN", "RAIN_SENSOR"}, detail))
            return tuple(sorted(result, key=lambda item: item.source))

    def _signal(self, snapshot: SourceSnapshot, evidence: Any) -> HazardSignal:
        hold = _time(snapshot.payload.get("hazardHoldUntil"))
        if hold is None or hold < snapshot.evaluated_at:
            hold = snapshot.evaluated_at + timedelta(minutes=15)
        return HazardSignal(f"{snapshot.source_id}:{evidence.reason_code}", snapshot.source_id,
            evidence.state, evidence.reason_code, evidence.reason, snapshot.evaluated_at, hold,
            source_input_id=snapshot.source_input_id,
            clear_condition="two fresh complete source cycles report no matching hazard")

    async def ingest(self, snapshot: SourceSnapshot) -> None:
        try:
            self._database.save_source_snapshot(snapshot)
        except RuntimeError:
            self._persistence_ok = False
        with self._lock:
            self._sources[snapshot.source_id] = snapshot
            if snapshot.state is SourceState.LIVE and snapshot.is_complete:
                self._last_good[snapshot.source_id] = snapshot
            present = set()
            for evidence in snapshot.evidence:
                signal = self._signal(snapshot, evidence)
                self._latches.observe(signal)
                present.add(signal.key)
            if snapshot.state is SourceState.LIVE and snapshot.is_complete:
                for hazard in self._latches.by_source(snapshot.source_id):
                    if hazard.signal.key not in present:
                        self._latches.observe_clear(hazard.signal.key, observed_at=snapshot.evaluated_at)
            else:
                self._latches.source_failed(snapshot.source_id)
        await self._publish(snapshot.evaluated_at, source=snapshot)

    async def mark_source_error(self, source_id: str, exc: Exception) -> None:
        now = utc_now()
        with self._lock:
            previous = self._sources[source_id]
            last = self._last_good.get(source_id)
            failed = SourceSnapshot(source_id, f"{source_id}:failure:{uuid4().hex}", previous.product,
                previous.cycle_time, previous.downloaded_at, previous.parsed_at, previous.valid_from,
                previous.valid_until, now, SourceState.FAILED, previous.content_sha256, False,
                previous.stale_after_seconds, previous.invalid_after_seconds,
                exc.code if isinstance(exc, SourceRunError) else "SOURCE_JOB_FAILED",
                exc.safe_message if isinstance(exc, SourceRunError) else type(exc).__name__,
                {"lastSuccessfulInputId": last.source_input_id if last else None,
                 "lastSuccessfulPayload": last.payload if last else None})
            self._sources[source_id] = failed
            self._latches.source_failed(source_id)
        try:
            self._database.save_source_snapshot(failed)
        except RuntimeError:
            self._persistence_ok = False
        await self._publish(now, source=failed)

    def _evaluate(self, at: datetime, *, persist: bool) -> DecisionSnapshot:
        if not self._persistence_ok:
            old = self._sources["LOCAL_PERSISTENCE"]
            self._sources["LOCAL_PERSISTENCE"] = SourceSnapshot("LOCAL_PERSISTENCE",
                f"LOCAL_PERSISTENCE:failed:{uuid4().hex}", "LOCAL_PERSISTENCE", None, None, None,
                None, None, at, SourceState.FAILED, None, False, old.stale_after_seconds,
                old.invalid_after_seconds, "PERSISTENCE_FAILED", "Local safety persistence is unavailable.")
        decision = evaluate_safety(evidence=self._latches.evidence(),
            source_health=self.source_health(now=at), equipment_state=self._config.equipment_state)
        previous = self._current
        snapshot = DecisionSnapshot.create(evaluation_at=at, algorithm_version=ALGORITHM_VERSION,
            configuration_version=str(self._config.configuration_version),
            equipment_state=self._config.equipment_state, decision=decision,
            active_hazards=self._latches.active(),
            source_snapshots=tuple(sorted(self._sources.values(), key=lambda item: item.source_id)))
        if persist and self._persistence_ok:
            try:
                self._database.save_decision_and_latches(snapshot)
            except RuntimeError:
                self._persistence_ok = False
                return self._evaluate(at, persist=False)
        self._current = snapshot
        event = self._alerts.evaluate(previous, snapshot)
        if event and self._persistence_ok:
            try:
                self._database.save_alert_event(event.to_dict())
            except RuntimeError:
                self._persistence_ok = False
        return snapshot

    async def _publish(self, at: datetime, *, source: SourceSnapshot | None = None) -> DecisionSnapshot:
        with self._lock:
            snapshot = self._evaluate(at, persist=True)
            alert = self._alerts.current
        if source:
            await self.events.publish("source-state", source.to_dict(at))
        await self.events.publish("snapshot", snapshot.to_dict(at))
        if alert and alert.snapshot_id == snapshot.snapshot_id:
            await self.events.publish("alert", alert.to_dict())
        return snapshot

    async def maintenance(self) -> None:
        now = utc_now()
        current = self._current
        repeat = self._alerts.repeat_if_due(current, now=now) if current else None
        await self._publish(now)
        if repeat:
            try:
                self._database.save_alert_event(repeat.to_dict())
            except RuntimeError:
                self._persistence_ok = False
            await self.events.publish("alert", repeat.to_dict())

    async def refresh(self, source: str | None = None) -> tuple[str, ...]:
        if source is not None and source not in self._runners:
            raise KeyError(source)
        selected = await self._scheduler.trigger(source)
        await self.events.publish("runtime-status", {"refreshRequested": selected, **self.runtime_dict()})
        return selected

    async def acknowledge(self) -> dict[str, Any]:
        now = utc_now()
        with self._lock:
            keys = self._latches.acknowledge_all(at=now)
            snapshot = self._evaluate(now, persist=True)
            event = self._alerts.acknowledge(snapshot, at=now)
        if self._persistence_ok:
            try:
                self._database.save_acknowledgement(snapshot_id=snapshot.snapshot_id,
                    acknowledged_at=now, hazard_keys=keys)
                self._database.save_alert_event(event.to_dict())
            except RuntimeError:
                self._persistence_ok = False
        await self.events.publish("snapshot", snapshot.to_dict(now))
        await self.events.publish("acknowledgement", event.to_dict())
        return event.to_dict()

    async def set_equipment_state(self, state: EquipmentState, *, configuration_version: int | None = None) -> DecisionSnapshot:
        updates: dict[str, object] = {"equipment_state": state}
        if configuration_version is not None:
            updates["configuration_version"] = configuration_version
        self._config = self._config.model_copy(update=updates)
        return await self._publish(utc_now())

    def snapshot(self) -> DecisionSnapshot:
        assert self._current is not None
        return self._current

    def snapshot_dict(self) -> dict[str, Any]:
        return self.snapshot().to_dict()

    def source_dicts(self) -> list[dict[str, Any]]:
        now = utc_now()
        return [item.to_dict(now) for item in sorted(self._sources.values(), key=lambda value: value.source_id)]

    def radar_dict(self) -> dict[str, Any]:
        return self._sources["DWD_RV"].to_dict()

    def warnings_dict(self) -> dict[str, Any]:
        return self._sources["DWD_CAP"].to_dict()

    def alerts_dict(self) -> dict[str, Any]:
        current = self._alerts.current
        return {"snapshotId": self.snapshot().snapshot_id,
            "active": current.to_dict() if current else None,
            "hazards": [item.to_dict() for item in self._latches.active()]}

    def runtime_dict(self) -> dict[str, Any]:
        return {"status": "RUNNING", "startedAt": iso(self._started_at),
            "scheduler": self._scheduler.status, "currentSnapshotId": self.snapshot().snapshot_id}

    def diagnostics_dict(self) -> dict[str, Any]:
        database = self._database.health()
        return {"runtime": self.runtime_dict(),
            "database": {"status": database.status, "schemaVersion": database.schema_version,
                "integrity": database.integrity, "error": database.error},
            "sources": self.source_dicts(),
            "activeLatches": [item.to_dict() for item in self._latches.active()],
            "alert": self.alerts_dict()}
