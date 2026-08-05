"""Structured comparison of the most recent trusted decision snapshots."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MAX_DECISIONS_TO_SCAN = 50


class ChangeSummaryError(RuntimeError):
    """Raised when persisted decision history cannot be read safely."""


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _boolean(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _source(snapshot: Mapping[str, Any], source_id: str) -> Mapping[str, Any]:
    sources = snapshot.get("sources")
    if not isinstance(sources, list):
        return {}
    for item in sources:
        source = _mapping(item)
        if source.get("sourceId") == source_id:
            return source
    return {}


def _radar(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(_source(snapshot, "DWD_RV").get("payload"))


def _warning_key(warning: Mapping[str, Any]) -> str:
    identifier = _text(warning.get("identifier"))
    if identifier is not None:
        return identifier
    parts = (
        _text(warning.get("event")) or "",
        _text(warning.get("headline")) or "",
        _text(warning.get("onset")) or "",
        _text(warning.get("expires")) or "",
    )
    return "|".join(parts)


def _warning_record(warning: Mapping[str, Any]) -> dict[str, str | None]:
    return {
        "id": _warning_key(warning),
        "event": _text(warning.get("event")),
        "headline": _text(warning.get("headline")),
        "severity": _text(warning.get("severity")),
    }


def _warnings(snapshot: Mapping[str, Any]) -> dict[str, dict[str, str | None]]:
    active = _mapping(_source(snapshot, "DWD_CAP").get("payload")).get("active")
    if not isinstance(active, list):
        return {}
    result: dict[str, dict[str, str | None]] = {}
    for item in active:
        warning = _mapping(item)
        record = _warning_record(warning)
        result[str(record["id"])] = record
    return result


def _source_states(snapshot: Mapping[str, Any]) -> dict[str, str]:
    explicit = snapshot.get("sourceStates")
    if isinstance(explicit, Mapping):
        return {
            str(key): str(value)
            for key, value in explicit.items()
            if isinstance(key, str) and isinstance(value, str)
        }
    result: dict[str, str] = {}
    sources = snapshot.get("sources")
    if not isinstance(sources, list):
        return result
    for item in sources:
        source = _mapping(item)
        source_id = _text(source.get("sourceId"))
        state = _text(source.get("state"))
        if source_id is not None and state is not None:
            result[source_id] = state
    return result


def _risk_state(snapshot: Mapping[str, Any]) -> str | None:
    return _text(_mapping(snapshot.get("hardwareRisk")).get("state"))


def _data_quality(snapshot: Mapping[str, Any]) -> str | None:
    return _text(_mapping(snapshot.get("hardwareRisk")).get("dataQuality"))


def is_complete_decision(snapshot: Mapping[str, Any]) -> bool:
    """Return whether a persisted decision is suitable for trusted comparison."""

    return _data_quality(snapshot) == "COMPLETE"


def load_recent_complete_decisions(
    database_path: Path,
    *,
    limit: int = 2,
) -> tuple[dict[str, Any], ...]:
    """Load recent complete decisions without mutating the runtime database."""

    if limit < 1:
        raise ValueError("limit must be positive")
    path = Path(database_path)
    if not path.exists():
        return ()
    try:
        connection = sqlite3.connect(path, timeout=5.0)
        try:
            connection.execute("PRAGMA query_only = ON")
            rows = connection.execute(
                """
                SELECT payload_json
                FROM decision_snapshots
                ORDER BY evaluation_at DESC
                LIMIT ?
                """,
                (MAX_DECISIONS_TO_SCAN,),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ChangeSummaryError(
            f"Decision history could not be read: {type(exc).__name__}"
        ) from exc

    decisions: list[dict[str, Any]] = []
    try:
        for row in rows:
            payload = json.loads(str(row[0]))
            if not isinstance(payload, dict):
                raise TypeError("decision payload is not an object")
            if is_complete_decision(payload):
                decisions.append(payload)
                if len(decisions) == limit:
                    break
    except (json.JSONDecodeError, TypeError) as exc:
        raise ChangeSummaryError(
            f"Decision history is invalid: {type(exc).__name__}"
        ) from exc
    return tuple(decisions)


def compare_decisions(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Return an additive, UI-neutral comparison of two trusted decisions."""

    previous_id = _text(previous.get("snapshotId")) if previous is not None else None
    current_id = _text(current.get("snapshotId"))
    current_risk = _risk_state(current)
    current_equipment = _text(current.get("equipmentState"))
    current_radar = _radar(current)
    current_distance = _number(current_radar.get("nearestPrecipitationDistanceKm"))
    current_arrival = _number(current_radar.get("arrivalMinutes"))
    current_rain = _boolean(current_radar.get("rainNow"))

    result: dict[str, Any] = {
        "hasPrevious": previous is not None,
        "previousSnapshotId": previous_id,
        "currentSnapshotId": current_id,
        "riskStateChanged": False,
        "previousRiskState": None,
        "currentRiskState": current_risk,
        "equipmentStateChanged": False,
        "previousEquipmentState": None,
        "currentEquipmentState": current_equipment,
        "warningsAdded": [],
        "warningsRemoved": [],
        "radarDistanceDeltaKm": None,
        "radarDistanceTrend": "NOT_COMPARABLE",
        "previousRadarDistanceKm": None,
        "currentRadarDistanceKm": current_distance,
        "arrivalChanged": False,
        "previousArrivalMinutes": None,
        "currentArrivalMinutes": current_arrival,
        "rainNowChanged": False,
        "previousRainNow": None,
        "currentRainNow": current_rain,
        "sourceStateChanges": [],
        "meaningfulChangeCount": 0,
        "noMeaningfulChange": False,
    }
    if previous is None:
        return result

    previous_risk = _risk_state(previous)
    previous_equipment = _text(previous.get("equipmentState"))
    previous_radar = _radar(previous)
    previous_distance = _number(previous_radar.get("nearestPrecipitationDistanceKm"))
    previous_arrival = _number(previous_radar.get("arrivalMinutes"))
    previous_rain = _boolean(previous_radar.get("rainNow"))

    result.update(
        {
            "riskStateChanged": previous_risk != current_risk,
            "previousRiskState": previous_risk,
            "equipmentStateChanged": previous_equipment != current_equipment,
            "previousEquipmentState": previous_equipment,
            "previousRadarDistanceKm": previous_distance,
            "previousArrivalMinutes": previous_arrival,
            "arrivalChanged": previous_arrival != current_arrival,
            "previousRainNow": previous_rain,
            "rainNowChanged": previous_rain != current_rain,
        }
    )

    if previous_distance is not None and current_distance is not None:
        delta = round(current_distance - previous_distance, 2)
        result["radarDistanceDeltaKm"] = delta
        if abs(delta) < 0.5:
            result["radarDistanceTrend"] = "STABLE"
        elif delta < 0:
            result["radarDistanceTrend"] = "CLOSER"
        else:
            result["radarDistanceTrend"] = "FARTHER"

    previous_warnings = _warnings(previous)
    current_warnings = _warnings(current)
    result["warningsAdded"] = [
        current_warnings[key]
        for key in sorted(current_warnings.keys() - previous_warnings.keys())
    ]
    result["warningsRemoved"] = [
        previous_warnings[key]
        for key in sorted(previous_warnings.keys() - current_warnings.keys())
    ]

    previous_states = _source_states(previous)
    current_states = _source_states(current)
    result["sourceStateChanges"] = [
        {
            "sourceId": source_id,
            "previous": previous_states.get(source_id),
            "current": current_states.get(source_id),
        }
        for source_id in sorted(previous_states.keys() | current_states.keys())
        if previous_states.get(source_id) != current_states.get(source_id)
    ]

    count = sum(
        (
            bool(result["riskStateChanged"]),
            bool(result["equipmentStateChanged"]),
            bool(result["arrivalChanged"]),
            bool(result["rainNowChanged"]),
        )
    )
    count += len(result["warningsAdded"])
    count += len(result["warningsRemoved"])
    count += len(result["sourceStateChanges"])
    delta = result["radarDistanceDeltaKm"]
    if isinstance(delta, (int, float)) and abs(float(delta)) >= 0.5:
        count += 1
    result["meaningfulChangeCount"] = count
    result["noMeaningfulChange"] = count == 0
    return result


def recent_change_summary(database_path: Path) -> dict[str, Any]:
    """Compare the latest complete decision with its trusted predecessor."""

    decisions = load_recent_complete_decisions(database_path, limit=2)
    if not decisions:
        return compare_decisions(None, {})
    current = decisions[0]
    previous = decisions[1] if len(decisions) > 1 else None
    return compare_decisions(previous, current)
