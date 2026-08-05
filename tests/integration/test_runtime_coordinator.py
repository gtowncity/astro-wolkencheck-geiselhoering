import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nowcast_service.config import AppConfig
from nowcast_service.decision_engine import EquipmentState, Evidence, RiskState, SourceState
from nowcast_service.runtime.coordinator import RuntimeCoordinator
from nowcast_service.runtime.models import SourceSnapshot
from nowcast_service.sources.runtime_base import SourceRunError


def source(
    source_id: str,
    *,
    evidence: tuple[Evidence, ...] = (),
    now: datetime | None = None,
    payload: dict[str, object] | None = None,
) -> SourceSnapshot:
    evaluated_at = now or datetime.now(UTC)
    return SourceSnapshot(
        source_id,
        f"{source_id}:{evaluated_at.timestamp()}",
        source_id,
        evaluated_at,
        evaluated_at,
        evaluated_at,
        evaluated_at,
        evaluated_at + timedelta(minutes=45),
        evaluated_at,
        SourceState.LIVE,
        "b" * 64,
        True,
        1200,
        2700,
        payload=(
            payload
            if payload is not None
            else {
                "hazardHoldUntil": (
                    evaluated_at + timedelta(minutes=30)
                ).isoformat()
            }
        ),
        evidence=evidence,
    )


class StaticRunner:
    def __init__(
        self,
        source_id: str,
        *,
        returned_source_id: str | None = None,
        evidence: tuple[Evidence, ...] = (),
    ) -> None:
        self.source_id = source_id
        self.returned_source_id = returned_source_id or source_id
        self.evidence = evidence
        self.calls = 0

    async def run(self, *, evaluated_at: datetime) -> SourceSnapshot:
        self.calls += 1
        return source(
            self.returned_source_id,
            evidence=self.evidence,
            now=evaluated_at,
        )


async def wait_until(predicate: object, *, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():  # type: ignore[operator]
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("runtime condition was not reached")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_green_failure_latch_restart_and_ack(tmp_path: Path) -> None:
    coordinator = RuntimeCoordinator(config=AppConfig(), data_dir=tmp_path)
    await coordinator.ingest(source("DWD_RV"))
    assert coordinator.snapshot().decision.state is RiskState.UNKNOWN
    await coordinator.ingest(source("DWD_CAP"))
    assert coordinator.snapshot().decision.state is RiskState.GREEN
    red = Evidence("DWD_RV", RiskState.RED, "RADAR_RAIN_AT_SITE", "Rain at site")
    await coordinator.ingest(source("DWD_RV", evidence=(red,)))
    await coordinator.acknowledge()
    await coordinator.mark_source_error("DWD_RV", SourceRunError("RV_FAIL", "failed"))
    assert coordinator.snapshot().decision.state is RiskState.RED
    restarted = RuntimeCoordinator(config=AppConfig(), data_dir=tmp_path)
    assert restarted.snapshot().decision.state is RiskState.RED
    assert restarted.snapshot().active_hazards[0].acknowledged_at is not None


@pytest.mark.asyncio
async def test_yellow_is_not_hidden_and_config_version_changes(tmp_path: Path) -> None:
    coordinator = RuntimeCoordinator(config=AppConfig(), data_dir=tmp_path)
    yellow = Evidence("DWD_RV", RiskState.YELLOW, "APPROACHING", "Approaching")
    await coordinator.ingest(source("DWD_RV", evidence=(yellow,)))
    await coordinator.mark_source_error("DWD_CAP", SourceRunError("CAP_FAIL", "failed"))
    assert coordinator.snapshot().decision.state is RiskState.YELLOW
    updated = await coordinator.set_equipment_state(
        EquipmentState.NOT_DEPLOYED, configuration_version=2
    )
    assert updated.configuration_version == "2"


def test_corrupt_database_never_boots_green(tmp_path: Path) -> None:
    path = tmp_path / "database" / "runtime.sqlite3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not sqlite")
    coordinator = RuntimeCoordinator(config=AppConfig(), data_dir=tmp_path)
    assert coordinator.snapshot().decision.state is RiskState.UNKNOWN
    assert (
        next(
            item for item in coordinator.source_health() if item.source == "LOCAL_PERSISTENCE"
        ).state
        is SourceState.FAILED
    )


@pytest.mark.asyncio
async def test_background_runtime_surface_refresh_and_shutdown(tmp_path: Path) -> None:
    radar = StaticRunner("DWD_RV")
    cap = StaticRunner("DWD_CAP")
    config = AppConfig().model_copy(
        update={
            "schedule": AppConfig().schedule.model_copy(
                update={"retry_attempts": 1, "jitter_seconds": 0}
            )
        }
    )
    coordinator = RuntimeCoordinator(
        config=config,
        data_dir=tmp_path,
        source_runners=(radar, cap),
    )

    await coordinator.start()
    try:
        await wait_until(
            lambda: coordinator.snapshot().decision.state is RiskState.GREEN
        )
        assert radar.calls >= 1
        assert cap.calls >= 1
        assert coordinator.runtime_dict()["status"] == "RUNNING"
        assert coordinator.runtime_dict()["currentSnapshotId"] == coordinator.snapshot().snapshot_id
        assert coordinator.radar_dict()["sourceId"] == "DWD_RV"
        assert coordinator.warnings_dict()["sourceId"] == "DWD_CAP"
        assert {item["sourceId"] for item in coordinator.source_dicts()} >= {
            "DWD_RV",
            "DWD_CAP",
            "LOCAL_PERSISTENCE",
        }
        assert coordinator.alerts_dict()["active"] is None
        diagnostics = coordinator.diagnostics_dict()
        assert diagnostics["runtime"]["currentSnapshotId"] == coordinator.snapshot().snapshot_id
        assert diagnostics["database"]["schemaVersion"] == 1

        selected = await coordinator.refresh("DWD_RV")
        assert selected == ("DWD_RV",)
        await wait_until(lambda: radar.calls >= 2)
    finally:
        await coordinator.stop()


@pytest.mark.asyncio
async def test_mismatched_runner_and_plain_exception_are_safe_failures(
    tmp_path: Path,
) -> None:
    config = AppConfig().model_copy(
        update={
            "schedule": AppConfig().schedule.model_copy(
                update={"retry_attempts": 1, "jitter_seconds": 0}
            )
        }
    )
    runner = StaticRunner("DWD_RV", returned_source_id="DWD_CAP")
    coordinator = RuntimeCoordinator(
        config=config,
        data_dir=tmp_path,
        source_runners=(runner,),
    )

    assert await coordinator.refresh("DWD_RV") == ("DWD_RV",)
    await wait_until(lambda: coordinator.radar_dict()["state"] == "FAILED")
    failed = coordinator.radar_dict()
    assert failed["failureCode"] == "SOURCE_JOB_FAILED"
    assert failed["failureMessage"] == "RuntimeError"
    assert failed["payload"]["lastSuccessfulInputId"] is None

    with pytest.raises(KeyError):
        await coordinator.refresh("missing")

    await coordinator.mark_source_error("DWD_CAP", ValueError("private detail"))
    cap = coordinator.warnings_dict()
    assert cap["failureCode"] == "SOURCE_JOB_FAILED"
    assert cap["failureMessage"] == "ValueError"
    assert "private detail" not in str(cap)


@pytest.mark.asyncio
async def test_source_health_ages_and_expired_hold_falls_back(tmp_path: Path) -> None:
    coordinator = RuntimeCoordinator(config=AppConfig(), data_dir=tmp_path)
    at = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    live = SourceSnapshot(
        "DWD_RV",
        "DWD_RV:aged",
        "DWD_RV",
        at,
        at,
        at,
        at,
        at + timedelta(minutes=30),
        at,
        SourceState.LIVE,
        "c" * 64,
        True,
        60,
        120,
    )
    await coordinator.ingest(live)

    stale = next(
        item
        for item in coordinator.source_health(now=at + timedelta(seconds=90))
        if item.source == "DWD_RV"
    )
    assert stale.state is SourceState.STALE
    assert stale.detail == "Source data exceeded its stale limit."

    invalid = next(
        item
        for item in coordinator.source_health(now=at + timedelta(seconds=180))
        if item.source == "DWD_RV"
    )
    assert invalid.state is SourceState.FAILED
    assert invalid.detail == "Source data exceeded its invalid limit."

    red = Evidence("DWD_RV", RiskState.RED, "RADAR_RAIN_AT_SITE", "Rain at site")
    await coordinator.ingest(
        source(
            "DWD_RV",
            evidence=(red,),
            now=at,
            payload={"hazardHoldUntil": (at - timedelta(minutes=1)).isoformat()},
        )
    )
    hazard = coordinator.snapshot().active_hazards[0]
    assert hazard.signal.hold_until == at + timedelta(minutes=15)

    clear_at = at + timedelta(minutes=16)
    await coordinator.ingest(source("DWD_RV", now=clear_at))
    assert coordinator.snapshot().active_hazards
    await coordinator.ingest(source("DWD_RV", now=clear_at + timedelta(seconds=1)))
    assert coordinator.snapshot().active_hazards == ()
