"""Fail-safe weather safety decision rules.

This module deliberately contains no network or UI code. It is the small,
testable core that combines already validated source evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class RiskState(StrEnum):
    RED = "RED"
    YELLOW = "YELLOW"
    GREEN = "GREEN"
    UNKNOWN = "UNKNOWN"


class DataQuality(StrEnum):
    COMPLETE = "COMPLETE"
    DEGRADED = "DEGRADED"
    INSUFFICIENT = "INSUFFICIENT"


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
    healthy: bool
    supporting: bool = False


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    state: RiskState
    data_quality: DataQuality
    action: Action
    reason_codes: tuple[str, ...]
    reasons: tuple[str, ...]


def _data_quality(source_health: Iterable[SourceHealth]) -> DataQuality:
    sources = tuple(source_health)
    required = tuple(source for source in sources if source.required)

    if any(not source.healthy for source in required):
        return DataQuality.INSUFFICIENT
    if any(not source.healthy for source in sources if source.supporting):
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
        }:
            return Action.CHECK_EQUIPMENT_IMMEDIATELY
        return Action.UNKNOWN_DO_NOT_RELY

    return Action.NO_LIVE_VETO_DETECTED


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
        selected = red
    elif yellow:
        state = RiskState.YELLOW
        selected = yellow
    elif quality is DataQuality.INSUFFICIENT:
        state = RiskState.UNKNOWN
        selected = ()
    else:
        state = RiskState.GREEN
        selected = ()

    return SafetyDecision(
        state=state,
        data_quality=quality,
        action=_action_for(state, equipment_state),
        reason_codes=tuple(item.reason_code for item in selected),
        reasons=tuple(item.reason for item in selected),
    )
