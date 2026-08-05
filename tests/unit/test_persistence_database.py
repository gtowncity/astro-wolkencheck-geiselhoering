import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nowcast_service.decision_engine import (
    Action,
    DataQuality,
    EquipmentState,
    RiskState,
    SafetyDecision,
    SourceState,
)
from nowcast_service.hazard_latch import HazardSignal, LatchedHazard
from nowcast_service.persistence.database import RuntimeDatabase
from nowcast_service.runtime.models import DecisionSnapshot, SourceSnapshot


def source(now: datetime) -> SourceSnapshot:
    return SourceSnapshot(
        "DWD_RV",
        "rv:1",
        "DWD_RV",
        now,
        now,
        now,
        now,
        now + timedelta(minutes=30),
        now,
        SourceState.LIVE,
        "a" * 64,
        True,
        900,
        1800,
    )


def test_database_round_trip_health_and_invalid_payload(tmp_path: Path) -> None:
    db = RuntimeDatabase(tmp_path)
    db.initialize()
    now = datetime(2026, 8, 5, 8, tzinfo=UTC)
    signal = HazardSignal(
        "rv:red",
        "DWD_RV",
        RiskState.RED,
        "R",
        "Rain",
        now,
        now + timedelta(minutes=30),
        source_input_id="rv:1",
    )
    hazard = LatchedHazard(signal, now, now)
    snapshot = DecisionSnapshot.create(
        evaluation_at=now,
        algorithm_version="test",
        configuration_version="1",
        equipment_state=EquipmentState.NOT_DEPLOYED,
        decision=SafetyDecision(
            RiskState.RED, DataQuality.COMPLETE, Action.DO_NOT_SETUP, ("R",), ("Rain",)
        ),
        active_hazards=(hazard,),
        source_snapshots=(source(now),),
    )
    db.save_source_snapshot(source(now))
    db.save_decision_and_latches(snapshot)
    assert db.load_active_hazards()[0].signal.key == "rv:red"
    assert db.health().status == "LIVE"
    with sqlite3.connect(db.path) as connection:
        connection.execute("DELETE FROM hazard_latches")
        connection.execute(
            "INSERT INTO hazard_latches VALUES (?,?,?,?)",
            ("bad", "DWD_RV", "RED", json.dumps({"bad": True})),
        )
        connection.commit()
    with pytest.raises(RuntimeError):
        db.load_active_hazards()
    assert db.health().status == "FAILED"
