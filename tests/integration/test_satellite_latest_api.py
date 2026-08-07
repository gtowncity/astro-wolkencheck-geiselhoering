import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from nowcast_service.app import create_app
from nowcast_service.satellite_image import PRODUCTS_BY_KEY
from nowcast_service.satellite_latest import LatestSatelliteImageResult


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (20, 10), (10, 20, 30)).save(output, format="PNG")
    return output.getvalue()


def test_satellite_image_without_time_uses_direct_latest_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = create_app(data_dir=tmp_path)
    retrieved_at = datetime.now(UTC).replace(microsecond=0)
    calls: list[dict[str, object]] = []

    async def fake_latest(**kwargs: object) -> LatestSatelliteImageResult:
        calls.append(kwargs)
        return LatestSatelliteImageResult(
            png=png_bytes(),
            product=PRODUCTS_BY_KEY["cloudtype"],
            retrieved_at=retrieved_at,
        )

    monkeypatch.setattr(
        "nowcast_service.app.render_latest_satellite_image",
        fake_latest,
    )
    response = TestClient(application).get(
        "/api/v1/satellite/image",
        params={
            "product": "cloudtype",
            "latitude": 48.5,
            "longitude": 12.5,
            "location_name": "Testort",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-satellite-provider"] == "EUMETSAT"
    assert response.headers["x-satellite-product"] == "cloudtype"
    assert response.headers["x-satellite-latest"] == "true"
    assert response.headers["x-satellite-cache"] == "BYPASS"
    assert response.headers["x-satellite-fresh"] == "unknown"
    assert response.headers["x-satellite-observation-time-source"] == "UNVERIFIED"
    assert "x-satellite-observation-time" not in response.headers
    assert "x-satellite-age-minutes" not in response.headers
    assert calls == [
        {
            "service": application.state.satellite_service,
            "product_key": "cloudtype",
            "latitude": 48.5,
            "longitude": 12.5,
            "location_name": "Testort",
        }
    ]
    assert Image.open(io.BytesIO(response.content)).size == (20, 10)


def test_verified_live_image_exposes_real_age_and_stale_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = create_app(data_dir=tmp_path)
    retrieved_at = datetime(2026, 8, 7, 7, 46, tzinfo=UTC)
    observed_at = retrieved_at - timedelta(minutes=56)

    async def fake_latest(**_: object) -> LatestSatelliteImageResult:
        return LatestSatelliteImageResult(
            png=png_bytes(),
            product=PRODUCTS_BY_KEY["cloudtype"],
            retrieved_at=retrieved_at,
            observed_at=observed_at,
            observation_time_source="DATA_STORE_WMS_PIXEL_MATCH",
        )

    monkeypatch.setattr(
        "nowcast_service.app.render_latest_satellite_image",
        fake_latest,
    )
    response = TestClient(application).get(
        "/api/v1/satellite/image",
        params={"product": "cloudtype"},
    )

    assert response.status_code == 200
    assert response.headers["x-satellite-observation-time"] == "2026-08-07T06:50:00Z"
    assert response.headers["x-satellite-age-minutes"] == "56.0"
    assert response.headers["x-satellite-fresh"] == "false"
    assert (
        response.headers["x-satellite-observation-time-source"]
        == "DATA_STORE_WMS_PIXEL_MATCH"
    )
