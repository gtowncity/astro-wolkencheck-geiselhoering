from datetime import UTC, datetime, timedelta

from nowcast_service.decision_engine import (
    EquipmentState,
    RiskState,
    SourceState,
)
from nowcast_service.hazard_latch import HazardSignal
from nowcast_service.runtime_state import RuntimeState


def test_runtime_state_updates_equipment_and_source_health() -> None:
    runtime = RuntimeState(equipment_state=EquipmentState.UNKNOWN)

    runtime.set_equipment_state(EquipmentState.NOT_DEPLOYED)
    runtime.set_source_state("DWD_RV", SourceState.LIVE)
    runtime.set_source_state("DWD_CAP", SourceState.LIVE)

    assert runtime.equipment_state is EquipmentState.NOT_DEPLOYED
    assert runtime.decision().state is RiskState.GREEN


def test_runtime_state_exposes_latched_hazard_after_source_failure() -> None:
    runtime = RuntimeState(equipment_state=EquipmentState.DEPLOYED_ATTENDED)
    now = datetime(2026, 8, 5, 0, 20, tzinfo=UTC)
    runtime.observe_hazard(
        HazardSignal(
            key="radar-arrival",
            source="DWD_RV",
            state=RiskState.RED,
            reason_code="RADAR_ARRIVAL_WITHIN_60_MIN",
            reason="Niederschlag erreicht den Standort in 43 Minuten.",
            observed_at=now,
            hold_until=now + timedelta(hours=1),
        )
    )

    runtime.set_source_state("DWD_RV", SourceState.FAILED, detail="Radarquelle ausgefallen.")

    decision = runtime.decision()
    assert decision.state is RiskState.RED
    assert decision.reason_codes == ("RADAR_ARRIVAL_WITHIN_60_MIN",)
    assert any(item.detail == "Radarquelle ausgefallen." for item in runtime.source_health())
