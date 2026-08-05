from datetime import UTC, datetime, timedelta
from pathlib import Path
import pytest
from nowcast_service.config import AppConfig
from nowcast_service.decision_engine import Evidence, EquipmentState, RiskState, SourceState
from nowcast_service.runtime.coordinator import RuntimeCoordinator
from nowcast_service.runtime.models import SourceSnapshot
from nowcast_service.sources.runtime_base import SourceRunError


def source(source_id: str, *, evidence: tuple[Evidence, ...] = ()) -> SourceSnapshot:
    now = datetime.now(UTC)
    return SourceSnapshot(source_id, f"{source_id}:{now.timestamp()}", source_id,
        now, now, now, now, now + timedelta(minutes=45), now, SourceState.LIVE,
        "b" * 64, True, 1200, 2700,
        payload={"hazardHoldUntil": (now + timedelta(minutes=30)).isoformat()},
        evidence=evidence)


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
    updated = await coordinator.set_equipment_state(EquipmentState.NOT_DEPLOYED,
        configuration_version=2)
    assert updated.configuration_version == "2"


def test_corrupt_database_never_boots_green(tmp_path: Path) -> None:
    path = tmp_path / "database" / "runtime.sqlite3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not sqlite")
    coordinator = RuntimeCoordinator(config=AppConfig(), data_dir=tmp_path)
    assert coordinator.snapshot().decision.state is RiskState.UNKNOWN
    assert next(item for item in coordinator.source_health()
        if item.source == "LOCAL_PERSISTENCE").state is SourceState.FAILED
