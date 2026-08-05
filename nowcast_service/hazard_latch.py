"""Persistent-capable hazard latching with explicit clear conditions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from nowcast_service.decision_engine import Evidence, RiskState


def _require_aware_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be expressed in UTC")


def _parse_utc(value: str, field: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require_aware_utc(parsed, field)
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class HazardSignal:
    key: str
    source: str
    state: RiskState
    reason_code: str
    reason: str
    observed_at: datetime
    hold_until: datetime
    clear_cycles_required: int = 2
    source_input_id: str | None = None
    clear_condition: str = "fresh source reports no matching hazard"

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
    acknowledged_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_aware_utc(self.latched_at, "latched_at")
        _require_aware_utc(self.last_confirmed_at, "last_confirmed_at")
        if self.acknowledged_at is not None:
            _require_aware_utc(self.acknowledged_at, "acknowledged_at")

    def to_evidence(self) -> Evidence:
        return Evidence(
            source=self.signal.source,
            state=self.signal.state,
            reason_code=self.signal.reason_code,
            reason=self.signal.reason,
            latched=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "hazardKey": self.signal.key,
            "source": self.signal.source,
            "state": self.signal.state,
            "reasonCode": self.signal.reason_code,
            "reason": self.signal.reason,
            "observedAt": self.signal.observed_at.isoformat(),
            "latchedAt": self.latched_at.isoformat(),
            "lastConfirmedAt": self.last_confirmed_at.isoformat(),
            "holdUntil": self.signal.hold_until.isoformat(),
            "clearStreak": self.clear_streak,
            "clearCyclesRequired": self.signal.clear_cycles_required,
            "acknowledgedAt": (
                self.acknowledged_at.isoformat() if self.acknowledged_at else None
            ),
            "sourceInputId": self.signal.source_input_id,
            "clearCondition": self.signal.clear_condition,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LatchedHazard:
        acknowledged = payload.get("acknowledgedAt")
        signal = HazardSignal(
            key=str(payload["hazardKey"]),
            source=str(payload["source"]),
            state=RiskState(str(payload["state"])),
            reason_code=str(payload["reasonCode"]),
            reason=str(payload["reason"]),
            observed_at=_parse_utc(str(payload["observedAt"]), "observedAt"),
            hold_until=_parse_utc(str(payload["holdUntil"]), "holdUntil"),
            clear_cycles_required=int(payload["clearCyclesRequired"]),
            source_input_id=(
                str(payload["sourceInputId"])
                if payload.get("sourceInputId") is not None
                else None
            ),
            clear_condition=str(payload.get("clearCondition") or "fresh clear evidence"),
        )
        return cls(
            signal=signal,
            latched_at=_parse_utc(str(payload["latchedAt"]), "latchedAt"),
            last_confirmed_at=_parse_utc(
                str(payload["lastConfirmedAt"]), "lastConfirmedAt"
            ),
            clear_streak=int(payload.get("clearStreak", 0)),
            acknowledged_at=(
                _parse_utc(str(acknowledged), "acknowledgedAt")
                if acknowledged is not None
                else None
            ),
        )


class HazardLatchRegistry:
    def __init__(self, hazards: tuple[LatchedHazard, ...] = ()) -> None:
        self._hazards = {item.signal.key: item for item in hazards}

    def observe(self, signal: HazardSignal) -> LatchedHazard:
        existing = self._hazards.get(signal.key)
        latched_at = existing.latched_at if existing is not None else signal.observed_at
        reset_ack = (
            existing is not None
            and (
                existing.signal.state != signal.state
                or existing.signal.reason_code != signal.reason_code
                or existing.signal.reason != signal.reason
            )
        )
        hazard = LatchedHazard(
            signal=signal,
            latched_at=latched_at,
            last_confirmed_at=signal.observed_at,
            clear_streak=0,
            acknowledged_at=(
                None if reset_ack or existing is None else existing.acknowledged_at
            ),
        )
        self._hazards[signal.key] = hazard
        return hazard

    def source_failed(self, source: str) -> None:
        del source

    def observe_clear(self, key: str, *, observed_at: datetime) -> bool:
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

    def acknowledge(self, key: str, *, at: datetime | None = None) -> bool:
        existing = self._hazards.get(key)
        if existing is None:
            return False
        acknowledged_at = at or datetime.now(UTC)
        _require_aware_utc(acknowledged_at, "acknowledged_at")
        self._hazards[key] = replace(existing, acknowledged_at=acknowledged_at)
        return True

    def acknowledge_all(self, *, at: datetime | None = None) -> tuple[str, ...]:
        acknowledged_at = at or datetime.now(UTC)
        keys = tuple(self._hazards)
        for key in keys:
            self.acknowledge(key, at=acknowledged_at)
        return tuple(sorted(keys))

    def by_source(self, source: str) -> tuple[LatchedHazard, ...]:
        return tuple(item for item in self.active() if item.signal.source == source)
