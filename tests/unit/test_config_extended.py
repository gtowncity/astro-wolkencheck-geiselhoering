from pathlib import Path

import pytest
from pydantic import ValidationError

from nowcast_service.config import AppConfig, ConfigStore, SafetyThresholds, default_data_dir


def test_cap_threshold_order_and_defaults() -> None:
    config = AppConfig()
    assert config.schedule.radar_interval_seconds == 300
    with pytest.raises(ValidationError):
        SafetyThresholds(cap_stale_after_minutes=30, cap_invalid_after_minutes=20)


def test_store_patch_and_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ConfigStore(tmp_path)
    store.save(AppConfig())
    assert store.patch({"port": 9876}).port == 9876
    monkeypatch.setenv("ASTRO_WOLKENCHECK_DATA_DIR", str(tmp_path / "private"))
    assert default_data_dir() == (tmp_path / "private").resolve()
