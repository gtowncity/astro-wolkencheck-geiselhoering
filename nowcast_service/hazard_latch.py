"""In-memory hazard latching with explicit, testable clear conditions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from nowcast_service.decision_engine import Evidence, RiskState


@dataclass(frozen=True, slots=True)
class HazardSignal:
    """A validated hazard observation that may need to outlive its source update."""

    key: str
    source: str
    state: RiskState
    reason_code: str
    reason: str
    observed_at: datetime
    hold_until: datetime
    clear_cycles_required: int = 2

    def __post_init__(self) -> None:
        if self.state not in {RiskState.RED, RiskState.YELLOW}:
            raise ValueError("Only RED or YELLOW hazards may be latched")
        if self.clear_cycles_required < 1:
            raise ValueError("clear_cycles_required must be at least 1")
        _require_aware_utc(self.observed_at, "observed_at")
        _require_aware_utc(self.hold_until, "hold_until")
        if self.hold_until < self.observed_at:
            raise ValueError("hold_until must not be before observed_at")


@dataclass(frozen=True, slots=True)
class LatchedHazard:
    signal: HazardSignal
    latched_at: datetime
    last_confirmed_at: datetime
    clear_streak: int = 0

    def to_evidence(self) -> Evidence:
        return Evidence(
            source=self.signal.source,
            state=self.signal.state,
            reason_code=self.signal.reason_code,
            reason=self.signal.reason,
            latched=True,
        )


def _require_aware_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be expressed in UTC")


class HazardLatchRegistry:
    """Keep hazards active until time and clear-cycle conditions are satisfied."""

    def __init__(self) -> None:
        self._hazards: dict[str, LatchedHazard] = {}

    def observe(self, signal: HazardSignal) -> LatchedHazard:
        existing = self._hazards.get(signal.key)
        latched_at = existing.latched_at if existing is not None else signal.observed_at
        hazard = LatchedHazard(
            signal=signal,
            latched_at=latched_at,
            last_confirmed_at=signal.observed_at,
            clear_streak=0,
        )
        self._hazards[signal.key] = hazard
        return hazard

    def source_failed(self, source: str) -> None:
        """Record no clear evidence; existing hazards intentionally remain unchanged."""

        del source

    def observe_clear(self, key: str, *, observed_at: datetime) -> bool:
        """Register one fresh clear cycle.

        Returns ``True`` only when the hazard was actually removed. A hazard may
        not clear before ``hold_until`` even if enough clear cycles were seen.
        """

        _require_aware_utc(observed_at, "observed_at")
        existing = self._hazards.get(key)
        if existing is None:
            return False

        next_streak = existing.clear_streak + 1
        eligible = (
            observed_at >= existing.signal.hold_until
            and next_streak >= existing.signal.clear_cycles_required
        )
        if eligible:
            del self._hazards[key]
            return True

        self._hazards[key] = replace(existing, clear_streak=next_streak)
        return False

    def active(self) -> tuple[LatchedHazard, ...]:
        return tuple(sorted(self._hazards.values(), key=lambda item: item.signal.key))

    def evidence(self) -> tuple[Evidence, ...]:
        return tuple(item.to_evidence() for item in self.active())

    def acknowledge(self, key: str) -> bool:
        """Acknowledge is deliberately non-destructive for safety state."""

        return key in self._hazards
