"""Small SQLite store for immutable snapshots, latches and alarm audit."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from nowcast_service.hazard_latch import LatchedHazard
from nowcast_service.runtime.models import DecisionSnapshot, SourceSnapshot, iso

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    status: str
    path: str
    schema_version: int | None
    integrity: str
    error: str | None = None


class RuntimeDatabase:
    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "database" / "runtime.sqlite3"
        self._lock = RLock()
        self._last_error: str | None = None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                try:
                    with self._connect() as connection:
                        row = connection.execute(
                            "SELECT version FROM schema_version LIMIT 1"
                        ).fetchone()
                        current = int(row[0]) if row is not None else 0
                except sqlite3.Error:
                    current = 0
                if current not in {0, SCHEMA_VERSION}:
                    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                    shutil.copy2(self.path, self.path.with_suffix(f".{stamp}.bak"))
            try:
                with self._connect() as connection:
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS schema_version (
                            version INTEGER NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS source_snapshots (
                            source_input_id TEXT PRIMARY KEY,
                            source_id TEXT NOT NULL,
                            evaluated_at TEXT NOT NULL,
                            state TEXT NOT NULL,
                            payload_json TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_source_snapshots_source_time
                            ON source_snapshots(source_id, evaluated_at DESC);
                        CREATE TABLE IF NOT EXISTS decision_snapshots (
                            snapshot_id TEXT PRIMARY KEY,
                            evaluation_at TEXT NOT NULL,
                            state TEXT NOT NULL,
                            payload_json TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_decisions_time
                            ON decision_snapshots(evaluation_at DESC);
                        CREATE TABLE IF NOT EXISTS hazard_latches (
                            hazard_key TEXT PRIMARY KEY,
                            source TEXT NOT NULL,
                            state TEXT NOT NULL,
                            payload_json TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS alert_events (
                            event_id TEXT PRIMARY KEY,
                            snapshot_id TEXT NOT NULL,
                            event_type TEXT NOT NULL,
                            risk_state TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            payload_json TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS acknowledgements (
                            acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                            snapshot_id TEXT NOT NULL,
                            acknowledged_at TEXT NOT NULL,
                            hazard_keys_json TEXT NOT NULL
                        );
                        """
                    )
                    row = connection.execute(
                        "SELECT version FROM schema_version LIMIT 1"
                    ).fetchone()
                    if row is None:
                        connection.execute(
                            "INSERT INTO schema_version(version) VALUES (?)",
                            (SCHEMA_VERSION,),
                        )
                    elif int(row[0]) != SCHEMA_VERSION:
                        connection.execute("DELETE FROM schema_version")
                        connection.execute(
                            "INSERT INTO schema_version(version) VALUES (?)",
                            (SCHEMA_VERSION,),
                        )
                    connection.commit()
                self._last_error = None
            except sqlite3.Error as exc:
                self._last_error = f"SQLite initialization failed: {type(exc).__name__}"
                raise RuntimeError(self._last_error) from exc

    def load_active_hazards(self) -> tuple[LatchedHazard, ...]:
        with self._lock:
            try:
                with self._connect() as connection:
                    rows = connection.execute(
                        "SELECT payload_json FROM hazard_latches ORDER BY hazard_key"
                    ).fetchall()
                hazards = tuple(LatchedHazard.from_dict(json.loads(str(row[0]))) for row in rows)
                self._last_error = None
                return hazards
            except (sqlite3.Error, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                self._last_error = f"Hazard persistence could not be read: {type(exc).__name__}"
                raise RuntimeError(self._last_error) from exc

    def save_source_snapshot(self, snapshot: SourceSnapshot) -> None:
        payload = json.dumps(snapshot.to_dict(snapshot.evaluated_at), sort_keys=True)
        with self._lock:
            try:
                with self._connect() as connection:
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO source_snapshots
                            (source_input_id, source_id, evaluated_at, state, payload_json)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            snapshot.source_input_id,
                            snapshot.source_id,
                            iso(snapshot.evaluated_at),
                            str(snapshot.state),
                            payload,
                        ),
                    )
                    connection.commit()
                self._last_error = None
            except sqlite3.Error as exc:
                self._last_error = f"Source snapshot persistence failed: {type(exc).__name__}"
                raise RuntimeError(self._last_error) from exc

    def save_decision_and_latches(self, snapshot: DecisionSnapshot) -> None:
        payload = json.dumps(snapshot.to_dict(snapshot.evaluation_at), sort_keys=True)
        with self._lock:
            try:
                with self._connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        """
                        INSERT INTO decision_snapshots
                            (snapshot_id, evaluation_at, state, payload_json)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            snapshot.snapshot_id,
                            iso(snapshot.evaluation_at),
                            str(snapshot.decision.state),
                            payload,
                        ),
                    )
                    connection.execute("DELETE FROM hazard_latches")
                    connection.executemany(
                        """
                        INSERT INTO hazard_latches
                            (hazard_key, source, state, payload_json)
                        VALUES (?, ?, ?, ?)
                        """,
                        [
                            (
                                hazard.signal.key,
                                hazard.signal.source,
                                str(hazard.signal.state),
                                json.dumps(hazard.to_dict(), sort_keys=True),
                            )
                            for hazard in snapshot.active_hazards
                        ],
                    )
                    connection.commit()
                self._last_error = None
            except sqlite3.Error as exc:
                self._last_error = f"Decision persistence failed: {type(exc).__name__}"
                raise RuntimeError(self._last_error) from exc

    def save_alert_event(self, payload: dict[str, Any]) -> None:
        with self._lock:
            try:
                with self._connect() as connection:
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO alert_events
                            (event_id, snapshot_id, event_type, risk_state,
                             created_at, payload_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(payload["eventId"]),
                            str(payload["snapshotId"]),
                            str(payload["eventType"]),
                            str(payload["riskState"]),
                            str(payload["createdAt"]),
                            json.dumps(payload, sort_keys=True),
                        ),
                    )
                    connection.commit()
                self._last_error = None
            except sqlite3.Error as exc:
                self._last_error = f"Alert persistence failed: {type(exc).__name__}"
                raise RuntimeError(self._last_error) from exc

    def save_acknowledgement(
        self,
        *,
        snapshot_id: str,
        acknowledged_at: datetime,
        hazard_keys: tuple[str, ...],
    ) -> None:
        with self._lock:
            try:
                with self._connect() as connection:
                    connection.execute(
                        """
                        INSERT INTO acknowledgements
                            (snapshot_id, acknowledged_at, hazard_keys_json)
                        VALUES (?, ?, ?)
                        """,
                        (
                            snapshot_id,
                            iso(acknowledged_at),
                            json.dumps(hazard_keys),
                        ),
                    )
                    connection.commit()
                self._last_error = None
            except sqlite3.Error as exc:
                self._last_error = f"Acknowledgement persistence failed: {type(exc).__name__}"
                raise RuntimeError(self._last_error) from exc

    def health(self) -> DatabaseHealth:
        with self._lock:
            if self._last_error is not None:
                return DatabaseHealth(
                    status="FAILED",
                    path=str(self.path.parent),
                    schema_version=None,
                    integrity="UNKNOWN",
                    error=self._last_error,
                )
            try:
                with self._connect() as connection:
                    row = connection.execute(
                        "SELECT version FROM schema_version LIMIT 1"
                    ).fetchone()
                    integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
                return DatabaseHealth(
                    status="LIVE" if integrity == "ok" else "FAILED",
                    path=str(self.path.parent),
                    schema_version=int(row[0]) if row else None,
                    integrity=integrity,
                    error=None if integrity == "ok" else "SQLite quick_check failed",
                )
            except sqlite3.Error as exc:
                return DatabaseHealth(
                    status="FAILED",
                    path=str(self.path.parent),
                    schema_version=None,
                    integrity="UNKNOWN",
                    error=f"SQLite health check failed: {type(exc).__name__}",
                )
