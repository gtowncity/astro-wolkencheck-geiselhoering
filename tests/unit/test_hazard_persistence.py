from datetime import UTC, datetime, timedelta

from nowcast_service.decision_engine import RiskState
from nowcast_service.hazard_latch import HazardLatchRegistry, HazardSignal, LatchedHazard


def signal(now: datetime, *, state: RiskState = RiskState.RED) -> HazardSignal:
    return HazardSignal(
        key="cap:storm",
        source="DWD_CAP",
        state=state,
        reason_code="CAP_STORM",
        reason="Storm",
        observed_at=now,
        hold_until=now + timedelta(minutes=10),
        source_input_id="cap:1",
        clear_condition="fresh CAP cancellation or expiry",
    )


def test_hazard_serialization_and_acknowledgement() -> None:
    now = datetime(2026, 8, 5, 8, tzinfo=UTC)
    registry = HazardLatchRegistry()
    registry.observe(signal(now))
    assert registry.acknowledge("missing", at=now) is False
    assert registry.acknowledge("cap:storm", at=now) is True
    restored = LatchedHazard.from_dict(registry.active()[0].to_dict())
    assert restored.acknowledged_at == now
    assert restored.signal.source_input_id == "cap:1"


def test_clear_requires_hold_and_two_fresh_cycles() -> None:
    now = datetime(2026, 8, 5, 8, tzinfo=UTC)
    registry = HazardLatchRegistry()
    registry.observe(signal(now))
    assert registry.observe_clear("cap:storm", observed_at=now + timedelta(minutes=11)) is False
    assert registry.observe_clear("cap:storm", observed_at=now + timedelta(minutes=12)) is True
    assert registry.active() == ()


def test_changed_hazard_resets_acknowledgement() -> None:
    now = datetime(2026, 8, 5, 8, tzinfo=UTC)
    registry = HazardLatchRegistry()
    registry.observe(signal(now, state=RiskState.YELLOW))
    registry.acknowledge_all(at=now)
    registry.observe(signal(now + timedelta(minutes=1), state=RiskState.RED))
    assert registry.active()[0].acknowledged_at is None
