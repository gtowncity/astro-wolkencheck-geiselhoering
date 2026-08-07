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


def test_live_api_reports_actual_product_after_freshness_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = create_app(data_dir=tmp_path)
    retrieved_at = datetime.now(UTC).replace(microsecond=0)
    observed_at = retrieved_at - timedelta(minutes=8)

    async def fake_latest(**_: object) -> LatestSatelliteImageResult:
        return LatestSatelliteImageResult(
            png=png_bytes(),
            product=PRODUCTS_BY_KEY["infrared"],
            retrieved_at=retrieved_at,
            observed_at=observed_at,
            observation_time_source="DATA_STORE_WMS_PIXEL_MATCH",
            requested_product=PRODUCTS_BY_KEY["cloudtype"],
            auto_fallback=True,
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
    assert response.headers["x-satellite-product"] == "infrared"
    assert response.headers["x-satellite-fresh"] == "true"
    assert response.headers["x-satellite-age-minutes"] == "8.0"
    assert response.headers["x-satellite-observation-time"] == observed_at.isoformat().replace(
        "+00:00", "Z"
    )
