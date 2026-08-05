"""Transition-based alarm evaluation; acknowledgement never changes safety."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from nowcast_service.decision_engine import RiskState
from nowcast_service.runtime.models import DecisionSnapshot, iso


class AlertEventType(StrEnum):
    RED = "RED"
    YELLOW = "YELLOW"
    ESCALATED = "ESCALATED"
    REASON_CHANGED = "REASON_CHANGED"
    REPEAT = "REPEAT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    CLEARED = "CLEARED"


@dataclass(frozen=True, slots=True)
class AlertEvent:
    event_id: str
    snapshot_id: str
    event_type: AlertEventType
    risk_state: RiskState
    reason_codes: tuple[str, ...]
    created_at: datetime
    requires_attention: bool
    acknowledged_at: datetime | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "eventId": self.event_id,
            "snapshotId": self.snapshot_id,
            "eventType": self.event_type,
            "riskState": self.risk_state,
            "reasonCodes": self.reason_codes,
            "createdAt": iso(self.created_at),
            "requiresAttention": self.requires_attention,
            "acknowledgedAt": iso(self.acknowledged_at),
        }


class AlertEngine:
    def __init__(self, *, red_repeat_seconds: int, yellow_repeat_seconds: int) -> None:
        self._repeat = {
            RiskState.RED: timedelta(seconds=red_repeat_seconds),
            RiskState.YELLOW: timedelta(seconds=yellow_repeat_seconds),
        }
        self._current: AlertEvent | None = None

    @property
    def current(self) -> AlertEvent | None:
        return self._current

    def evaluate(
        self,
        previous: DecisionSnapshot | None,
        current: DecisionSnapshot,
    ) -> AlertEvent | None:
        now = current.evaluation_at
        old_state = previous.decision.state if previous is not None else RiskState.UNKNOWN
        new_state = current.decision.state
        event_type: AlertEventType | None = None
        if new_state is RiskState.RED:
            if old_state is RiskState.YELLOW:
                event_type = AlertEventType.ESCALATED
            elif old_state is not RiskState.RED:
                event_type = AlertEventType.RED
            elif previous is not None and set(previous.decision.reason_codes) != set(
                current.decision.reason_codes
            ):
                event_type = AlertEventType.REASON_CHANGED
        elif new_state is RiskState.YELLOW:
            if old_state not in {RiskState.RED, RiskState.YELLOW}:
                event_type = AlertEventType.YELLOW
            elif (
                old_state is RiskState.YELLOW
                and previous is not None
                and set(previous.decision.reason_codes) != set(current.decision.reason_codes)
            ):
                event_type = AlertEventType.REASON_CHANGED
        elif old_state in {RiskState.RED, RiskState.YELLOW}:
            event_type = AlertEventType.CLEARED

        if event_type is None:
            return None
        event = AlertEvent(
            event_id=uuid4().hex,
            snapshot_id=current.snapshot_id,
            event_type=event_type,
            risk_state=new_state,
            reason_codes=current.decision.reason_codes,
            created_at=now,
            requires_attention=new_state in {RiskState.RED, RiskState.YELLOW},
        )
        self._current = event
        return event

    def repeat_if_due(self, snapshot: DecisionSnapshot, *, now: datetime) -> AlertEvent | None:
        current = self._current
        risk = snapshot.decision.state
        if current is None or risk not in self._repeat or current.acknowledged_at is not None:
            return None
        if now - current.created_at < self._repeat[risk]:
            return None
        event = AlertEvent(
            event_id=uuid4().hex,
            snapshot_id=snapshot.snapshot_id,
            event_type=AlertEventType.REPEAT,
            risk_state=risk,
            reason_codes=snapshot.decision.reason_codes,
            created_at=now,
            requires_attention=True,
        )
        self._current = event
        return event

    def acknowledge(self, snapshot: DecisionSnapshot, *, at: datetime | None = None) -> AlertEvent:
        now = at or datetime.now(UTC)
        current = self._current
        if current is not None:
            current = replace(current, acknowledged_at=now, requires_attention=False)
        event = AlertEvent(
            event_id=uuid4().hex,
            snapshot_id=snapshot.snapshot_id,
            event_type=AlertEventType.ACKNOWLEDGED,
            risk_state=snapshot.decision.state,
            reason_codes=snapshot.decision.reason_codes,
            created_at=now,
            requires_attention=False,
            acknowledged_at=now,
        )
        self._current = current or event
        return event
