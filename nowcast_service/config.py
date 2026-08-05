"""Typed local configuration stored outside the repository."""

from __future__ import annotations

import json
import os
from enum import StrEnum
from pathlib import Path
from typing import Any

from platformdirs import user_data_path
from pydantic import BaseModel, ConfigDict, Field, model_validator

from nowcast_service.decision_engine import EquipmentState


class LocationPrecision(StrEnum):
    DEFAULT_APPROXIMATE = "DEFAULT_APPROXIMATE"
    PRIVATE_EXACT = "PRIVATE_EXACT"


class LocationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "Geiselhöring"
    latitude: float = Field(default=48.84, ge=-90, le=90)
    longitude: float = Field(default=12.40, ge=-180, le=180)
    timezone: str = "Europe/Berlin"
    precision: LocationPrecision = LocationPrecision.DEFAULT_APPROXIMATE


class SafetyThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    radar_red_arrival_minutes: int = Field(default=60, ge=5, le=120)
    radar_yellow_arrival_minutes: int = Field(default=120, ge=15, le=240)
    radar_stale_after_minutes: int = Field(default=15, ge=5, le=60)
    radar_invalid_after_minutes: int = Field(default=30, ge=10, le=180)

    @model_validator(mode="after")
    def validate_ordering(self) -> SafetyThresholds:
        if self.radar_yellow_arrival_minutes <= self.radar_red_arrival_minutes:
            raise ValueError("yellow arrival window must be greater than red window")
        if self.radar_invalid_after_minutes <= self.radar_stale_after_minutes:
            raise ValueError("invalid age must be greater than stale age")
        return self


class RainSensorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    adapter: str = "disabled"

    @model_validator(mode="after")
    def validate_adapter(self) -> RainSensorConfig:
        if self.enabled and self.adapter == "disabled":
            raise ValueError("an enabled rain sensor needs a real or mock adapter")
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    location: LocationConfig = Field(default_factory=LocationConfig)
    thresholds: SafetyThresholds = Field(default_factory=SafetyThresholds)
    rain_sensor: RainSensorConfig = Field(default_factory=RainSensorConfig)
    equipment_state: EquipmentState = EquipmentState.UNKNOWN
    bind_host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)


class ConfigStore:
    """Load and atomically persist local configuration."""

    def __init__(self, data_dir: Path | None = None) -> None:
        root = data_dir or default_data_dir()
        self.data_dir = Path(root)
        self.path = self.data_dir / "config" / "config.json"

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return AppConfig.model_validate(raw)

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        payload = config.model_dump(mode="json")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def patch(self, updates: dict[str, Any]) -> AppConfig:
        current = self.load()
        merged = current.model_dump(mode="python")
        merged.update(updates)
        updated = AppConfig.model_validate(merged)
        self.save(updated)
        return updated


def default_data_dir() -> Path:
    override = os.environ.get("ASTRO_WOLKENCHECK_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(user_data_path("AstroWolkencheck", appauthor=False))
