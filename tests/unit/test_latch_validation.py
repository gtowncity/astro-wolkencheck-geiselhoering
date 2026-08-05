from dataclasses import replace
from datetime import UTC, datetime

import pytest

from nowcast_service.decision_engine import RiskState
from nowcast_service.hazard_latch import HazardLatchRegistry, HazardSignal


def sample() -> HazardSignal:
    start = datetime(2026, 8, 5, 0, 20, tzinfo=UTC)
    end = datetime(2026, 8, 5, 1, 20, tzinfo=UTC)
    return HazardSignal(
        key="sample",
        source="DWD_RV",
        state=RiskState.RED,
        reason_code="RADAR_SAMPLE",
        reason="Testsignal.",
        observed_at=start,
        hold_until=end,
    )


def test_registry_returns_false_for_unknown_items() -> None:
    registry = HazardLatchRegistry()
    now = datetime(2026, 8, 5, 1, 30, tzinfo=UTC)

    assert registry.observe_clear("missing", observed_at=now) is False
    assert registry.acknowledge("missing") is False


def test_signal_validation_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="RED or YELLOW"):
        replace(sample(), state=RiskState.GREEN)
    with pytest.raises(ValueError, match="at least 1"):
        replace(sample(), clear_cycles_required=0)
    with pytest.raises(ValueError, match="hold_until"):
        replace(sample(), hold_until=datetime(2026, 8, 5, 0, 19, tzinfo=UTC))


def test_signal_validation_requires_utc() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(sample(), observed_at=datetime(2026, 8, 5, 0, 20))
    with pytest.raises(ValueError, match="UTC"):
        replace(sample(), observed_at=datetime.fromisoformat("2026-08-05T02:20:00+02:00"))
