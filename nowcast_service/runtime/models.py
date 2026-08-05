"""Immutable runtime snapshots shared by API, persistence and alarms."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from nowcast_service.decision_engine import (
    EquipmentState,
    Evidence,
    SafetyDecision,
    SourceState,
)
from nowcast_service.hazard_latch import LatchedHazard


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must be expressed in UTC")


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    source_id: str
    source_input_id: str
    product: str
    cycle_time: datetime | None
    downloaded_at: datetime | None
    parsed_at: datetime | None
    valid_from: datetime | None
    valid_until: datetime | None
    evaluated_at: datetime
    state: SourceState
    content_sha256: str | None
    is_complete: bool
    stale_after_seconds: int
    invalid_after_seconds: int
    failure_code: str | None = None
    failure_message: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()

    def __post_init__(self) -> None:
        require_utc(self.evaluated_at, "evaluated_at")
        for name, value in (
            ("cycle_time", self.cycle_time),
            ("downloaded_at", self.downloaded_at),
            ("parsed_at", self.parsed_at),
            ("valid_from", self.valid_from),
            ("valid_until", self.valid_until),
        ):
            if value is not None:
                require_utc(value, name)
        if self.stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        if self.invalid_after_seconds <= self.stale_after_seconds:
            raise ValueError("invalid_after_seconds must exceed stale_after_seconds")
        if self.state is SourceState.LIVE and not self.is_complete:
            raise ValueError("LIVE source snapshots must be complete")

    def reference_time(self) -> datetime:
        return self.cycle_time or self.parsed_at or self.downloaded_at or self.evaluated_at

    def age_seconds(self, now: datetime | None = None) -> int:
        current = now or utc_now()
        require_utc(current, "now")
        return max(0, int((current - self.reference_time()).total_seconds()))

    def effective_state(self, now: datetime | None = None) -> SourceState:
        if self.state is not SourceState.LIVE:
            return self.state
        age = self.age_seconds(now)
        if age > self.invalid_after_seconds:
            return SourceState.FAILED
        if age > self.stale_after_seconds:
            return SourceState.STALE
        return SourceState.LIVE

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        state = self.effective_state(now)
        return {
            "sourceId": self.source_id,
            "sourceInputId": self.source_input_id,
            "product": self.product,
            "cycleTime": iso(self.cycle_time),
            "downloadedAt": iso(self.downloaded_at),
            "parsedAt": iso(self.parsed_at),
            "validFrom": iso(self.valid_from),
            "validUntil": iso(self.valid_until),
            "evaluatedAt": iso(self.evaluated_at),
            "ageSeconds": self.age_seconds(now),
            "state": state,
            "failureCode": self.failure_code,
            "failureMessage": self.failure_message,
            "contentSha256": self.content_sha256,
            "isComplete": self.is_complete,
            "staleAfterSeconds": self.stale_after_seconds,
            "invalidAfterSeconds": self.invalid_after_seconds,
            "payload": self.payload,
        }


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    snapshot_id: str
    evaluation_at: datetime
    algorithm_version: str
    configuration_version: str
    equipment_state: EquipmentState
    decision: SafetyDecision
    active_hazards: tuple[LatchedHazard, ...]
    source_snapshots: tuple[SourceSnapshot, ...]

    def __post_init__(self) -> None:
        require_utc(self.evaluation_at, "evaluation_at")

    @classmethod
    def create(
        cls,
        *,
        evaluation_at: datetime,
        algorithm_version: str,
        configuration_version: str,
        equipment_state: EquipmentState,
        decision: SafetyDecision,
        active_hazards: tuple[LatchedHazard, ...],
        source_snapshots: tuple[SourceSnapshot, ...],
    ) -> DecisionSnapshot:
        return cls(
            snapshot_id=uuid4().hex,
            evaluation_at=evaluation_at,
            algorithm_version=algorithm_version,
            configuration_version=configuration_version,
            equipment_state=equipment_state,
            decision=decision,
            active_hazards=active_hazards,
            source_snapshots=source_snapshots,
        )

    @property
    def earliest_possible_clear_at(self) -> datetime | None:
        if not self.active_hazards:
            return None
        return min(item.signal.hold_until for item in self.active_hazards)

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        return {
            "schemaVersion": "1.1",
            "snapshotId": self.snapshot_id,
            "evaluationAt": iso(self.evaluation_at),
            "generatedAt": iso(now or utc_now()),
            "algorithmVersion": self.algorithm_version,
            "configurationVersion": self.configuration_version,
            "equipmentState": self.equipment_state,
            "hardwareRisk": {
                "state": self.decision.state,
                "dataQuality": self.decision.data_quality,
                "action": self.decision.action,
                "reasonCodes": self.decision.reason_codes,
                "reasons": self.decision.reasons,
            },
            "activeHazards": [item.to_dict() for item in self.active_hazards],
            "sourceStates": {
                item.source_id: item.effective_state(now) for item in self.source_snapshots
            },
            "sourceInputIds": {
                item.source_id: item.source_input_id for item in self.source_snapshots
            },
            "sources": [item.to_dict(now) for item in self.source_snapshots],
            "earliestPossibleClearAt": iso(self.earliest_possible_clear_at),
        }
