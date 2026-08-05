from pathlib import Path

from fastapi.testclient import TestClient

from nowcast_service.app import create_app


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


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


def test_root_serves_existing_forecast_application_with_live_assets(tmp_path: Path) -> None:
    response = client(tmp_path).get("/")

    assert response.status_code == 200
    assert "<title>Astro Wolkencheck - Geiselhöring</title>" in response.text
    assert '<link rel="stylesheet" href="/local-live.css">' in response.text
    assert '<link rel="stylesheet" href="/local-live-timeline.css">' in response.text
    assert '<script src="/local-live.js" defer></script>' in response.text
    assert '<script src="/local-live-timeline.js" defer></script>' in response.text
    assert response.headers["x-content-type-options"] == "nosniff"


def test_local_live_timeline_assets_are_served(tmp_path: Path) -> None:
    test_client = client(tmp_path)

    script = test_client.get("/local-live-timeline.js")
    stylesheet = test_client.get("/local-live-timeline.css")

    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert "awc-timeline-path" in script.text
    assert "frame.rainAtSite" in script.text

    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert ".awc-timeline-chart" in stylesheet.text
    assert '[data-rain="true"]' in stylesheet.text
