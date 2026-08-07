from datetime import UTC, datetime

from nowcast_service.satellite_image import PRODUCTS_BY_KEY
from nowcast_service.satellite_latest import LatestSatelliteImageResult


def test_latest_result_keeps_observation_and_retrieval_times_separate() -> None:
    observed_at = datetime(2026, 8, 7, 7, 10, tzinfo=UTC)
    retrieved_at = datetime(2026, 8, 7, 7, 16, tzinfo=UTC)

    result = LatestSatelliteImageResult(
        png=b"png",
        product=PRODUCTS_BY_KEY["cloudtype"],
        retrieved_at=retrieved_at,
        observed_at=observed_at,
        observation_time_source="DATA_STORE_WMS_PIXEL_MATCH",
    )

    assert result.observed_at == observed_at
    assert result.retrieved_at == retrieved_at
    assert result.observation_time_source == "DATA_STORE_WMS_PIXEL_MATCH"
