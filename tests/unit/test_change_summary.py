import json
import sqlite3
from pathlib import Path

import pytest

from nowcast_service.runtime.change_summary import (
    compare_decisions,
    load_recent_complete_decisions,
    recent_change_summary,
)


def decision(
    snapshot_id: str,
    *,
    state: str = "GREEN",
    quality: str = "COMPLETE",
    equipment: str = "NOT_DEPLOYED",
    distance: float | None = 80.0,
    arrival: float | None = None,
    rain_now: bool = False,
    warnings: list[dict[str, object]] | None = None,
    cap_state: str = "LIVE",
) -> dict[str, object]:
    return {
        "snapshotId": snapshot_id,
        "equipmentState": equipment,
        "hardwareRisk": {
            "state": state,
            "dataQuality": quality,
        },
        "sourceStates": {
            "DWD_RV": "LIVE",
            "DWD_CAP": cap_state,
        },
        "sources": [
            {
                "sourceId": "DWD_RV",
                "payload": {
                    "nearestPrecipitationDistanceKm": distance,
                    "arrivalMinutes": arrival,
                    "rainNow": rain_now,
                },
            },
            {
                "sourceId": "DWD_CAP",
                "payload": {"active": warnings or []},
            },
        ],
    }


def create_history_database(path: Path, snapshots: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE decision_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                evaluation_at TEXT NOT NULL,
                state TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        for index, payload in enumerate(snapshots):
            connection.execute(
                """
                INSERT INTO decision_snapshots
                    (snapshot_id, evaluation_at, state, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    payload["snapshotId"],
                    f"2026-08-05T09:{index:02d}:00Z",
                    payload["hardwareRisk"]["state"],
                    json.dumps(payload),
                ),
            )
        connection.commit()
    finally:
        connection.close()


def test_first_complete_decision_has_no_comparison() -> None:
    summary = compare_decisions(None, decision("current"))

    assert summary["hasPrevious"] is False
    assert summary["currentSnapshotId"] == "current"
    assert summary["meaningfulChangeCount"] == 0
    assert summary["noMeaningfulChange"] is False


def test_comparison_reports_risk_radar_warning_and_source_changes() -> None:
    previous = decision("previous")
    current = decision(
        "current",
        state="YELLOW",
        equipment="DEPLOYED_ATTENDED",
        distance=68.0,
        arrival=35.0,
        warnings=[
            {
                "identifier": "storm",
                "event": "GEWITTER",
                "headline": "Warnung vor Gewitter",
                "severity": "Moderate",
            }
        ],
        cap_state="STALE",
    )

    summary = compare_decisions(previous, current)

    assert summary["hasPrevious"] is True
    assert summary["riskStateChanged"] is True
    assert summary["previousRiskState"] == "GREEN"
    assert summary["currentRiskState"] == "YELLOW"
    assert summary["equipmentStateChanged"] is True
    assert summary["radarDistanceDeltaKm"] == -12.0
    assert summary["radarDistanceTrend"] == "CLOSER"
    assert summary["arrivalChanged"] is True
    assert summary["warningsAdded"][0]["headline"] == "Warnung vor Gewitter"
    assert summary["sourceStateChanges"] == [
        {
            "sourceId": "DWD_CAP",
            "previous": "LIVE",
            "current": "STALE",
        }
    ]
    assert summary["meaningfulChangeCount"] == 6
    assert summary["noMeaningfulChange"] is False


def test_small_distance_jitter_is_not_meaningful() -> None:
    previous = decision("previous", distance=80.0)
    current = decision("current", distance=80.3)

    summary = compare_decisions(previous, current)

    assert summary["radarDistanceDeltaKm"] == pytest.approx(0.3)
    assert summary["radarDistanceTrend"] == "STABLE"
    assert summary["meaningfulChangeCount"] == 0
    assert summary["noMeaningfulChange"] is True


def test_loader_skips_incomplete_decisions_and_returns_newest_first(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime.sqlite3"
    snapshots = [
        decision("old-complete"),
        decision("incomplete", quality="INSUFFICIENT"),
        decision("new-complete", distance=70.0),
    ]
    create_history_database(database, snapshots)

    loaded = load_recent_complete_decisions(database, limit=2)

    assert [item["snapshotId"] for item in loaded] == [
        "new-complete",
        "old-complete",
    ]
    summary = recent_change_summary(database)
    assert summary["previousSnapshotId"] == "old-complete"
    assert summary["currentSnapshotId"] == "new-complete"
    assert summary["radarDistanceTrend"] == "CLOSER"


def test_loader_requires_positive_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        load_recent_complete_decisions(tmp_path / "missing.sqlite3", limit=0)
