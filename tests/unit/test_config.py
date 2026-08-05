import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from nowcast_service.config import AppConfig, ConfigStore, SafetyThresholds
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


def test_invalid_threshold_order_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SafetyThresholds(
            radar_red_arrival_minutes=60,
            radar_yellow_arrival_minutes=60,
        )
