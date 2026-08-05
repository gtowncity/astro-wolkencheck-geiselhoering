import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from nowcast_service.config import (
    AppConfig,
    ConfigStore,
    RainSensorConfig,
    SafetyThresholds,
    default_data_dir,
)
from nowcast_service.decision_engine import EquipmentState


def test_default_config_is_fail_safe() -> None:
    config = AppConfig()

    assert config.equipment_state is EquipmentState.UNKNOWN
    assert config.bind_host == "127.0.0.1"
    assert config.rain_sensor.enabled is False


def test_config_round_trip_uses_atomic_local_file(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path)
    config = AppConfig(equipment_state=EquipmentState.NOT_DEPLOYED)

    store.save(config)

    assert store.load() == config
    assert store.path.exists()
    assert not store.path.with_suffix(".json.tmp").exists()
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert saved["equipment_state"] == "NOT_DEPLOYED"


def test_patch_merges_and_persists_top_level_values(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path)

    updated = store.patch({"equipment_state": EquipmentState.NOT_DEPLOYED, "port": 9876})

    assert updated.equipment_state is EquipmentState.NOT_DEPLOYED
    assert updated.port == 9876
    assert store.load() == updated


def test_missing_config_returns_defaults(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path)

    assert store.load() == AppConfig()


def test_environment_override_controls_private_data_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASTRO_WOLKENCHECK_DATA_DIR", str(tmp_path / "private"))

    assert default_data_dir() == (tmp_path / "private").resolve()


def test_invalid_threshold_order_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SafetyThresholds(
            radar_red_arrival_minutes=60,
            radar_yellow_arrival_minutes=60,
        )

    with pytest.raises(ValidationError):
        SafetyThresholds(
            radar_stale_after_minutes=30,
            radar_invalid_after_minutes=30,
        )


def test_enabled_sensor_cannot_use_disabled_adapter() -> None:
    with pytest.raises(ValidationError):
        RainSensorConfig(enabled=True, adapter="disabled")
