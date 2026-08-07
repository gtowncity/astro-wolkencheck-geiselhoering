from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from nowcast_service.satellite_image import SatelliteImageService, SatelliteProduct
from nowcast_service.satellite_latest import (
    _LatestCandidate,
    render_latest_satellite_image,
)


@pytest.mark.asyncio
async def test_stale_cloudtype_prefers_fresh_coloured_rgb_before_infrared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = datetime.now(UTC).replace(microsecond=0)
    calls: list[str] = []

    async def fake_candidate(
        *, service: SatelliteImageService, product: SatelliteProduct
    ) -> _LatestCandidate:
        del service
        key = product.key
        calls.append(key)
        ages = {
            "cloudtype": 70,
            "cloudphase": 8,
            "geocolour": 12,
            "infrared": 3,
        }
        return _LatestCandidate(
            image=Image.new("RGB", (360, 255), (20, 30, 40)),
            product=product,
            retrieved_at=reference,
            observed_at=reference - timedelta(minutes=ages[key]),
            observation_time_source="DATA_STORE_WMS_PIXEL_MATCH",
        )

    async def fake_display(
        product: SatelliteProduct, observed_at: datetime | None
    ) -> Image.Image:
        assert product.key == "cloudphase"
        assert observed_at == reference - timedelta(minutes=8)
        return Image.new("RGB", (2400, 1700), (20, 30, 40))

    monkeypatch.setattr(
        "nowcast_service.satellite_latest._load_latest_candidate",
        fake_candidate,
    )
    monkeypatch.setattr(
        "nowcast_service.satellite_latest._download_display_frame",
        fake_display,
    )

    result = await render_latest_satellite_image(
        service=SatelliteImageService(tmp_path),
        product_key="cloudtype",
        latitude=48.84,
        longitude=12.40,
        location_name="Geiselhöring",
    )

    assert calls[0] == "cloudtype"
    assert set(calls[1:]) == {"cloudphase", "geocolour", "infrared"}
    assert result.product.key == "cloudphase"
    assert result.requested_product is not None
    assert result.requested_product.key == "cloudtype"
    assert result.auto_fallback is True
    assert result.observed_at == reference - timedelta(minutes=8)


@pytest.mark.asyncio
async def test_fresh_requested_product_is_not_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = datetime.now(UTC).replace(microsecond=0)
    calls: list[str] = []

    async def fake_candidate(
        *, service: SatelliteImageService, product: SatelliteProduct
    ) -> _LatestCandidate:
        del service
        calls.append(product.key)
        return _LatestCandidate(
            image=Image.new("RGB", (360, 255), (20, 30, 40)),
            product=product,
            retrieved_at=reference,
            observed_at=reference - timedelta(minutes=8),
            observation_time_source="DATA_STORE_WMS_PIXEL_MATCH",
        )

    async def fake_display(
        product: SatelliteProduct, observed_at: datetime | None
    ) -> Image.Image:
        assert product.key == "cloudtype"
        assert observed_at == reference - timedelta(minutes=8)
        return Image.new("RGB", (2400, 1700), (20, 30, 40))

    monkeypatch.setattr(
        "nowcast_service.satellite_latest._load_latest_candidate",
        fake_candidate,
    )
    monkeypatch.setattr(
        "nowcast_service.satellite_latest._download_display_frame",
        fake_display,
    )

    result = await render_latest_satellite_image(
        service=SatelliteImageService(tmp_path),
        product_key="cloudtype",
        latitude=48.84,
        longitude=12.40,
        location_name="Geiselhöring",
    )

    assert calls == ["cloudtype"]
    assert result.product.key == "cloudtype"
    assert result.auto_fallback is False
