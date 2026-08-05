"""Thread-safe in-memory state used by the local API and background jobs."""

from __future__ import annotations

from dataclasses import replace
from threading import RLock

from nowcast_service.decision_engine import (
    EquipmentState,
    SafetyDecision,
    SourceHealth,
    SourceState,
    evaluate_safety,
)
from nowcast_service.hazard_latch import HazardLatchRegistry, HazardSignal


class RuntimeState:
    def __init__(self, *, equipment_state: EquipmentState) -> None:
        self._lock = RLock()
        self._equipment_state = equipment_state
        self._sources: dict[str, SourceHealth] = {
            "DWD_RV": SourceHealth(
                source="DWD_RV",
                required=True,
                state=SourceState.INITIALIZING,
                detail="Radardaten wurden noch nicht erfolgreich geladen.",
            ),
            "DWD_CAP": SourceHealth(
                source="DWD_CAP",
                required=True,
                state=SourceState.INITIALIZING,
                detail="Amtliche Warnungen wurden noch nicht erfolgreich geladen.",
            ),
            "DWD_WN": SourceHealth(
                source="DWD_WN",
                required=False,
                supporting=True,
                state=SourceState.NOT_AVAILABLE,
            ),
            "RAIN_SENSOR": SourceHealth(
                source="RAIN_SENSOR",
                required=False,
                supporting=True,
                state=SourceState.DISABLED,
            ),
        }
        self._latches = HazardLatchRegistry()

    @property
    def equipment_state(self) -> EquipmentState:
        with self._lock:
            return self._equipment_state

    def set_equipment_state(self, state: EquipmentState) -> None:
        with self._lock:
            self._equipment_state = state

    def source_health(self) -> tuple[SourceHealth, ...]:
        with self._lock:
            return tuple(sorted(self._sources.values(), key=lambda item: item.source))

    def set_source_state(
        self,
        source: str,
        state: SourceState,
        *,
        detail: str | None = None,
    ) -> None:
        with self._lock:
            existing = self._sources[source]
            self._sources[source] = replace(existing, state=state, detail=detail)
            if state is SourceState.FAILED:
                self._latches.source_failed(source)

    def observe_hazard(self, signal: HazardSignal) -> None:
        with self._lock:
            self._latches.observe(signal)

    def decision(self) -> SafetyDecision:
        with self._lock:
            return evaluate_safety(
                evidence=self._latches.evidence(),
                source_health=self._sources.values(),
                equipment_state=self._equipment_state,
            )
