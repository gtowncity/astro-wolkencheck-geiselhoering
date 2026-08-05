from dataclasses import replace
from datetime import UTC, datetime

from nowcast_service.decision_engine import RiskState
from nowcast_service.hazard_latch import HazardLatchRegistry, HazardSignal


def utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 5, hour, minute, tzinfo=UTC)


def radar_red() -> HazardSignal:
    return HazardSignal(
        key="radar-arrival",
        source="DWD_RV",
        state=RiskState.RED,
        reason_code="RADAR_ARRIVAL_WITHIN_60_MIN",
        reason="Niederschlag erreicht den Standort voraussichtlich in 43 Minuten.",
        observed_at=utc(0, 20),
        hold_until=utc(1, 15),
        clear_cycles_required=2,
    )


def test_source_failure_does_not_remove_latched_hazard() -> None:
    registry = HazardLatchRegistry()
    registry.observe(radar_red())

    registry.source_failed("DWD_RV")

    assert len(registry.active()) == 1
    assert registry.evidence()[0].latched is True
    assert registry.evidence()[0].state is RiskState.RED


def test_hazard_cannot_clear_before_hold_until() -> None:
    registry = HazardLatchRegistry()
    registry.observe(radar_red())

    assert registry.observe_clear("radar-arrival", observed_at=utc(0, 30)) is False
    assert registry.observe_clear("radar-arrival", observed_at=utc(0, 35)) is False
    assert len(registry.active()) == 1


def test_hazard_requires_fresh_clear_cycles_after_hold_window() -> None:
    registry = HazardLatchRegistry()
    registry.observe(radar_red())

    assert registry.observe_clear("radar-arrival", observed_at=utc(1, 15)) is False
    assert registry.observe_clear("radar-arrival", observed_at=utc(1, 20)) is True
    assert registry.active() == ()


def test_reconfirmation_resets_clear_streak_and_may_extend_hold() -> None:
    registry = HazardLatchRegistry()
    registry.observe(radar_red())
    registry.observe_clear("radar-arrival", observed_at=utc(1, 15))

    updated = replace(
        radar_red(),
        observed_at=utc(1, 16),
        hold_until=utc(2, 0),
    )
    registry.observe(updated)

    active = registry.active()[0]
    assert active.clear_streak == 0
    assert active.signal.hold_until == utc(2, 0)


def test_acknowledgement_does_not_remove_hazard() -> None:
    registry = HazardLatchRegistry()
    registry.observe(radar_red())

    assert registry.acknowledge("radar-arrival") is True
    assert len(registry.active()) == 1


def test_naive_timestamps_are_rejected() -> None:
    try:
        HazardSignal(
            key="bad",
            source="DWD_RV",
            state=RiskState.RED,
            reason_code="BAD",
            reason="bad",
            observed_at=datetime(2026, 8, 5, 0, 20),
            hold_until=utc(1, 0),
        )
    except ValueError as exc:
        assert "observed_at" in str(exc)
    else:
        raise AssertionError("Naive timestamp was accepted")
