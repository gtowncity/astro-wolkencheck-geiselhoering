"""Fail-safe weather safety decision rules.

This module deliberately contains no network or UI code. It is the small,
testable core that combines already validated source evidence.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class RiskState(StrEnum):
    RED = "RED"
    YELLOW = "YELLOW"
    GREEN = "GREEN"
    UNKNOWN = "UNKNOWN"


class DataQuality(StrEnum):
    COMPLETE = "COMPLETE"
    DEGRADED = "DEGRADED"
    INSUFFICIENT = "INSUFFICIENT"


class SourceState(StrEnum):
    INITIALIZING = "INITIALIZING"
    LIVE = "LIVE"
    STALE = "STALE"
    FAILED = "FAILED"
    DISABLED = "DISABLED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class EquipmentState(StrEnum):
    NOT_DEPLOYED = "NOT_DEPLOYED"
    DEPLOYED_ATTENDED = "DEPLOYED_ATTENDED"
    DEPLOYED_UNATTENDED = "DEPLOYED_UNATTENDED"
    UNKNOWN = "UNKNOWN"


class Action(StrEnum):
    DO_NOT_SETUP = "DO_NOT_SETUP"
    STOP_AND_PROTECT = "STOP_AND_PROTECT"
    CHECK_EQUIPMENT_IMMEDIATELY = "CHECK_EQUIPMENT_IMMEDIATELY"
    WAIT_AND_RECHECK = "WAIT_AND_RECHECK"
    SETUP_POSSIBLE_WITH_CAUTION = "SETUP_POSSIBLE_WITH_CAUTION"
    NO_LIVE_VETO_DETECTED = "NO_LIVE_VETO_DETECTED"
    UNKNOWN_DO_NOT_RELY = "UNKNOWN_DO_NOT_RELY"
    NOT_CONNECTED = "NOT_CONNECTED"


@dataclass(frozen=True, slots=True)
class Evidence:
    source: str
    state: RiskState
    reason_code: str
    reason: str
    latched: bool = False


@dataclass(frozen=True, slots=True)
class SourceHealth:
    source: str
    required: bool
    state: SourceState
    supporting: bool = False
    detail: str | None = None

    @property
    def healthy_for_green(self) -> bool:
        """Return whether this source may participate in a GREEN decision."""

        return self.state is SourceState.LIVE

    @property
    def optional_and_inactive(self) -> bool:
        """Return whether an optional source is intentionally not in use."""

        return not self.required and self.state in {
            SourceState.DISABLED,
            SourceState.NOT_CONFIGURED,
            SourceState.NOT_AVAILABLE,
        }


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    state: RiskState
    data_quality: DataQuality
    action: Action
    reason_codes: tuple[str, ...]
    reasons: tuple[str, ...]


def _required_failures(source_health: tuple[SourceHealth, ...]) -> tuple[SourceHealth, ...]:
    required = tuple(source for source in source_health if source.required)
    if not required:
        return (
            SourceHealth(
                source="SAFETY_CORE",
                required=True,
                state=SourceState.NOT_CONFIGURED,
                detail="Keine erforderlichen Live-Quellen sind konfiguriert.",
            ),
        )
    return tuple(source for source in required if not source.healthy_for_green)


def _data_quality(source_health: tuple[SourceHealth, ...]) -> DataQuality:
    if _required_failures(source_health):
        return DataQuality.INSUFFICIENT

    degraded_supporting = any(
        source.supporting and not source.healthy_for_green and not source.optional_and_inactive
        for source in source_health
    )
    if degraded_supporting:
        return DataQuality.DEGRADED
    return DataQuality.COMPLETE


def _action_for(state: RiskState, equipment: EquipmentState) -> Action:
    if state is RiskState.RED:
        if equipment is EquipmentState.NOT_DEPLOYED:
            return Action.DO_NOT_SETUP
        if equipment in {
            EquipmentState.DEPLOYED_ATTENDED,
            EquipmentState.DEPLOYED_UNATTENDED,
        }:
            return Action.STOP_AND_PROTECT
        return Action.CHECK_EQUIPMENT_IMMEDIATELY

    if state is RiskState.YELLOW:
        if equipment is EquipmentState.DEPLOYED_UNATTENDED:
            return Action.CHECK_EQUIPMENT_IMMEDIATELY
        return Action.WAIT_AND_RECHECK

    if state is RiskState.UNKNOWN:
        if equipment in {
            EquipmentState.DEPLOYED_ATTENDED,
            EquipmentState.DEPLOYED_UNATTENDED,
            EquipmentState.UNKNOWN,
        }:
            return Action.CHECK_EQUIPMENT_IMMEDIATELY
        return Action.UNKNOWN_DO_NOT_RELY

    return Action.NO_LIVE_VETO_DETECTED


def _unknown_reasons(failures: tuple[SourceHealth, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    codes: list[str] = []
    reasons: list[str] = []
    for source in failures:
        codes.append(f"SOURCE_{source.source}_{source.state}")
        reasons.append(
            source.detail
            or (
                f"Erforderliche Quelle {source.source} ist nicht frisch und gueltig "
                f"({source.state})."
            )
        )
    return tuple(codes), tuple(reasons)


def evaluate_safety(
    *,
    evidence: Iterable[Evidence],
    source_health: Iterable[SourceHealth],
    equipment_state: EquipmentState,
) -> SafetyDecision:
    """Combine validated evidence according to the fail-safe priority rules.

    Confirmed or latched hazards always remain visible. Missing required data
    prevents GREEN, but never hides a known RED or YELLOW hazard.
    """

    items = tuple(evidence)
    health = tuple(source_health)
    quality = _data_quality(health)

    red = tuple(item for item in items if item.state is RiskState.RED)
    yellow = tuple(item for item in items if item.state is RiskState.YELLOW)

    if red:
        state = RiskState.RED
        codes = tuple(item.reason_code for item in red)
        reasons = tuple(item.reason for item in red)
    elif yellow:
        state = RiskState.YELLOW
        codes = tuple(item.reason_code for item in yellow)
        reasons = tuple(item.reason for item in yellow)
    else:
        failures = _required_failures(health)
        if failures:
            state = RiskState.UNKNOWN
            codes, reasons = _unknown_reasons(failures)
        else:
            state = RiskState.GREEN
            codes = ()
            reasons = ()

    return SafetyDecision(
        state=state,
        data_quality=quality,
        action=_action_for(state, equipment_state),
        reason_codes=codes,
        reasons=reasons,
    )
