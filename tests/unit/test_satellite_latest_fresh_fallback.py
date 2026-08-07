from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from nowcast_service.satellite_image import SatelliteImageService
from nowcast_service.satellite_latest import (
    _LatestCandidate,
    render_latest_satellite_image,
)


@pytest.mark.asyncio
async def test_stale_cloudtype_automatically_uses_fresh_infrared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = datetime.now(UTC).replace(microsecond=0)
    calls: list[str] = []

    async def fake_candidate(*, service: object, product: object) -> _LatestCandidate:
        del service
        key = getattr(product, "key")
        calls.append(key)
        age = 70 if key == "cloudtype" else 8
        return _LatestCandidate(
            image=Image.new("RGB", (1200, 850), (20, 30, 40)),
            product=product,
            retrieved_at=reference,
            observed_at=reference - timedelta(minutes=age),
            observation_time_source="DATA_STORE_WMS_PIXEL_MATCH",
        )

    monkeypatch.setattr(
        "nowcast_service.satellite_latest._load_latest_candidate",
        fake_candidate,
    )

    result = await render_latest_satellite_image(
        service=SatelliteImageService(tmp_path),
        product_key="cloudtype",
        latitude=48.84,
        longitude=12.40,
        location_name="Geiselhöring",
    )

    assert calls == ["cloudtype", "infrared"]
    assert result.product.key == "infrared"
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

    async def fake_candidate(*, service: object, product: object) -> _LatestCandidate:
        del service
        key = getattr(product, "key")
        calls.append(key)
        return _LatestCandidate(
            image=Image.new("RGB", (1200, 850), (20, 30, 40)),
            product=product,
            retrieved_at=reference,
            observed_at=reference - timedelta(minutes=8),
            observation_time_source="DATA_STORE_WMS_PIXEL_MATCH",
        )

    monkeypatch.setattr(
        "nowcast_service.satellite_latest._load_latest_candidate",
        fake_candidate,
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
