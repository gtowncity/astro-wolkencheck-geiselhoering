import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from nowcast_service.app import create_app
from nowcast_service.satellite_image import (
    PRODUCTS_BY_KEY,
    SatelliteImageError,
    SatelliteImageResult,
)


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (20, 10), (10, 20, 30)).save(output, format="PNG")
    return output.getvalue()


def test_initial_safety_is_unknown_and_never_green(tmp_path: Path) -> None:
    response = client(tmp_path).get("/api/v1/safety")

    assert response.status_code == 200
    payload = response.json()
    assert payload["hardwareRisk"]["state"] == "UNKNOWN"
    assert payload["hardwareRisk"]["action"] == "CHECK_EQUIPMENT_IMMEDIATELY"
    assert set(payload["hardwareRisk"]["reasonCodes"]) == {
        "SOURCE_DWD_CAP_INITIALIZING",
        "SOURCE_DWD_RV_INITIALIZING",
    }
    assert response.headers["cache-control"] == "no-store"


def test_meta_liveness_and_readiness_are_explicit(tmp_path: Path) -> None:
    test_client = client(tmp_path)

    meta = test_client.get("/api/v1/meta")
    live = test_client.get("/api/v1/health/live")
    ready = test_client.get("/api/v1/health/ready")

    assert meta.status_code == 200
    assert meta.json()["mode"] == "LOCAL"
    assert live.json()["status"] == "LIVE"
    assert ready.json()["status"] == "READY"
    assert ready.json()["safetyDataReady"] is False


def test_runtime_config_explicitly_identifies_local_mode(tmp_path: Path) -> None:
    response = client(tmp_path).get("/runtime-config.json")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "LOCAL",
        "localApiAvailable": True,
        "apiBase": "/api/v1",
        "browserAudioEnabled": True,
        "browserNotificationsEnabled": True,
    }


def test_session_patch_requires_csrf(tmp_path: Path) -> None:
    test_client = client(tmp_path)

    denied = test_client.patch(
        "/api/v1/session",
        json={"equipment_state": "NOT_DEPLOYED"},
    )

    assert denied.status_code == 403


def test_session_patch_rejects_foreign_origin(tmp_path: Path) -> None:
    test_client = client(tmp_path)
    csrf = test_client.get("/api/v1/security/csrf").json()["token"]

    denied = test_client.patch(
        "/api/v1/session",
        headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
        json={"equipment_state": "NOT_DEPLOYED"},
    )

    assert denied.status_code == 403
    assert denied.json()["detail"] == "Origin not allowed"


def test_session_patch_persists_equipment_state(tmp_path: Path) -> None:
    test_client = client(tmp_path)
    csrf = test_client.get("/api/v1/security/csrf").json()["token"]

    response = test_client.patch(
        "/api/v1/session",
        headers={"X-CSRF-Token": csrf},
        json={"equipment_state": "NOT_DEPLOYED"},
    )

    assert response.status_code == 200
    assert response.json()["equipmentState"] == "NOT_DEPLOYED"

    restarted = client(tmp_path)
    assert restarted.get("/api/v1/session").json()["equipmentState"] == "NOT_DEPLOYED"


def test_public_config_does_not_expose_coordinates(tmp_path: Path) -> None:
    payload = client(tmp_path).get("/api/v1/config/public").json()

    assert "latitude" not in payload["location"]
    assert "longitude" not in payload["location"]


def test_change_summary_is_empty_before_first_complete_decision(tmp_path: Path) -> None:
    response = client(tmp_path).get("/api/v1/changes")

    assert response.status_code == 200
    payload = response.json()
    assert payload["hasPrevious"] is False
    assert payload["currentSnapshotId"] is None
    assert payload["meaningfulChangeCount"] == 0


def test_root_defers_forecast_and_serves_recovery_assets(tmp_path: Path) -> None:
    response = client(tmp_path).get("/")

    assert response.status_code == 200
    assert "<title>Astro Wolkencheck - Geiselhöring</title>" in response.text
    assert "await refreshData();" not in response.text
    assert "Zeitraum prüfen und Forecast laden." in response.text
    assert "timeoutMs: 10000, retries: 0, concurrency: 8" in response.text
    assert "Forecast laden" in response.text
    for asset in (
        "/local-live.css",
        "/local-live-timeline.css",
        "/local-live-details.css",
        "/local-live-navigation.css",
        "/local-ui-recovery.css",
        "/local-satellite-viewer.css",
        "/local-live.js",
        "/local-live-timeline.js",
        "/local-live-details.js",
        "/local-live-changes.js",
        "/local-live-navigation.js",
        "/local-ui-recovery.js",
        "/local-satellite-viewer.js",
    ):
        assert asset in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "fullscreen=(self)" in response.headers["permissions-policy"]


def test_local_live_enhancement_assets_are_served(tmp_path: Path) -> None:
    test_client = client(tmp_path)
    checks = {
        "/local-live-timeline.js": "awc-timeline-path",
        "/local-live-timeline.css": ".awc-timeline-chart",
        "/local-live-details.js": "awc-warning-facts",
        "/local-live-details.css": ".awc-alarm-capabilities",
        "/local-live-changes.js": "SERVER_HISTORY",
        "/local-live-navigation.js": 'nowcast: "JETZT"',
        "/local-live-navigation.css": ".awc-planning-windows",
        "/local-ui-recovery.js": "Forecast-Ort wurde noch nicht bestätigt",
        "/local-ui-recovery.css": ".awc-location-control",
        "/local-satellite-viewer.js": "toggleFullscreen",
        "/local-satellite-viewer.css": ".awc-satellite-viewer:fullscreen",
    }

    for path, marker in checks.items():
        response = test_client.get(path)
        assert response.status_code == 200
        assert marker in response.text


def test_satellite_metadata_endpoint_adds_local_location(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = create_app(data_dir=tmp_path)
    observed = "2026-08-05T12:00:00Z"

    async def fake_metadata(product: str) -> dict[str, object]:
        return {
            "provider": "EUMETSAT",
            "selectedProduct": {"key": product},
            "products": [],
            "frames": [observed],
        }

    monkeypatch.setattr(application.state.satellite_service, "metadata", fake_metadata)
    response = TestClient(application).get("/api/v1/satellite/meta?product=infrared")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "EUMETSAT"
    assert payload["selectedProduct"]["key"] == "infrared"
    assert payload["location"]["name"] == "Geiselhöring"
    assert isinstance(payload["location"]["latitude"], float)


def test_satellite_image_endpoint_returns_real_png_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = create_app(data_dir=tmp_path)
    observed = datetime.now(UTC).replace(microsecond=0)

    async def fake_render(**kwargs: object) -> SatelliteImageResult:
        assert kwargs["product_key"] == "geocolour"
        assert kwargs["latitude"] == pytest.approx(48.5)
        assert kwargs["longitude"] == pytest.approx(12.5)
        assert kwargs["location_name"] == "Testort"
        return SatelliteImageResult(
            png=png_bytes(),
            observed_at=observed,
            product=PRODUCTS_BY_KEY["geocolour"],
            cached=False,
        )

    monkeypatch.setattr(application.state.satellite_service, "render", fake_render)
    response = TestClient(application).get(
        "/api/v1/satellite/image",
        params={
            "product": "geocolour",
            "time": observed.isoformat().replace("+00:00", "Z"),
            "latitude": 48.5,
            "longitude": 12.5,
            "location_name": "Testort",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-satellite-provider"] == "EUMETSAT"
    assert response.headers["x-satellite-fresh"] == "true"
    assert Image.open(io.BytesIO(response.content)).size == (20, 10)


def test_satellite_api_handles_invalid_or_unavailable_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = create_app(data_dir=tmp_path)
    test_client = TestClient(application)
    invalid = test_client.get("/api/v1/satellite/image?time=not-a-time")
    assert invalid.status_code == 400

    async def unavailable(_: str) -> dict[str, object]:
        raise SatelliteImageError("offline")

    monkeypatch.setattr(application.state.satellite_service, "metadata", unavailable)
    metadata = test_client.get("/api/v1/satellite/meta")
    assert metadata.status_code == 503


def test_unknown_local_asset_is_not_exposed(tmp_path: Path) -> None:
    response = client(tmp_path).get("/not-a-dashboard-asset.js")

    assert response.status_code == 404
    assert response.json()["detail"] == "Asset not found"
