from pathlib import Path

from fastapi.testclient import TestClient

from nowcast_service.app import create_app


def test_api_contracts_share_snapshot_and_hide_coordinates(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))
    safety = client.get("/api/v1/safety").json()
    assert safety["hardwareRisk"]["state"] == "UNKNOWN"
    assert client.get("/api/v1/nowcast").json()["snapshotId"] == safety["snapshotId"]
    public = client.get("/api/v1/config/public").json()
    assert "latitude" not in str(public) and "longitude" not in str(public)
    assert client.get("/runtime-config.json").json()["mode"] == "LOCAL"
    assert client.get("/api/v1/safety").headers["cache-control"] == "no-store"


def test_writes_require_csrf_and_ack_does_not_change_risk(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))
    assert client.post("/api/v1/alerts/acknowledge").status_code == 403
    token = client.get("/api/v1/security/csrf").json()["token"]
    before = client.get("/api/v1/safety").json()["hardwareRisk"]["state"]
    response = client.post("/api/v1/alerts/acknowledge", headers={"X-CSRF-Token": token})
    assert response.status_code == 200
    assert client.get("/api/v1/safety").json()["hardwareRisk"]["state"] == before
    denied = client.patch(
        "/api/v1/session",
        headers={"X-CSRF-Token": token, "Origin": "https://evil.example"},
        json={"equipment_state": "NOT_DEPLOYED"},
    )
    assert denied.status_code == 403


def test_local_page_injects_live_panel_assets(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir=tmp_path))
    root = client.get("/")
    assert root.status_code == 200 and "/local-live.js" in root.text
    assert client.get("/local-live.js").status_code == 200
    assert client.get("/local-live.css").status_code == 200
