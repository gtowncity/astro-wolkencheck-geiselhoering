import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from PIL import Image

from nowcast_service.satellite_image import (
    CAPABILITIES_FALLBACK_AGE,
    EUMETVIEW_WMS_URL,
    FRESHNESS_LIMIT,
    PRODUCTS_BY_KEY,
    SatelliteFrame,
    SatelliteImageError,
    SatelliteImageService,
    SatelliteProduct,
    _draw_location_pin,
    _parse_duration,
    _validated_image,
    _wms_bbox,
    location_to_pixel,
    parse_time_dimension,
)


def png_bytes(size: tuple[int, int] = (120, 80)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, (40, 80, 120)).save(output, format="PNG")
    return output.getvalue()


def capabilities_xml() -> bytes:
    return b"""<?xml version='1.0' encoding='UTF-8'?>
<WMS_Capabilities xmlns='http://www.opengis.net/wms' version='1.3.0'>
  <Capability><Layer>
    <Layer><Name>mtg_fd:rgb_geocolour</Name><Title>GeoColour</Title>
      <Dimension name='time'>2026-08-05T10:00:00Z/2026-08-05T12:00:00Z/PT10M</Dimension>
    </Layer>
    <Layer><Name>mtg_fd:ir105_hrfi</Name><Title>Infrared</Title>
      <Dimension name='time'>2026-08-05T11:40:00Z,2026-08-05T11:50:00Z</Dimension>
    </Layer>
    <Layer><Name>mtg_fd:rgb_cloudtype</Name><Title>Cloud Type</Title>
      <Extent name='time'>2026-08-05T11:40:00Z,2026-08-05T11:50:00Z</Extent>
    </Layer>
  </Layer></Capability>
</WMS_Capabilities>"""


def test_parse_duration_and_time_dimension() -> None:
    assert _parse_duration("P1DT2H3M4S") == timedelta(
        days=1,
        hours=2,
        minutes=3,
        seconds=4,
    )
    with pytest.raises(ValueError, match="Unsupported"):
        _parse_duration("monthly")

    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    frames = parse_time_dimension(
        "2026-08-05T10:00:00Z/2026-08-05T12:30:00Z/PT10M",
        now=now,
        limit=4,
    )
    assert [frame.iso() for frame in frames] == [
        "2026-08-05T11:30:00Z",
        "2026-08-05T11:40:00Z",
        "2026-08-05T11:50:00Z",
        "2026-08-05T12:00:00Z",
    ]

    mixed = parse_time_dimension(
        "invalid,2026-08-05T11:40:00Z,2026-08-05T11:50:00Z",
        now=now,
    )
    assert [frame.iso() for frame in mixed] == [
        "2026-08-05T11:40:00Z",
        "2026-08-05T11:50:00Z",
    ]


def test_location_projection_and_wms_axis_order() -> None:
    x, y = location_to_pixel(
        latitude=48.84,
        longitude=12.40,
        width=1200,
        height=850,
    )
    assert 0 < x < 1200
    assert 0 < y < 850
    assert _wms_bbox() == "47.0,8.75,50.75,14.05"

    with pytest.raises(ValueError, match="longitude"):
        location_to_pixel(latitude=48.84, longitude=20.0)
    with pytest.raises(ValueError, match="latitude"):
        location_to_pixel(latitude=55.0, longitude=12.40)


def test_image_validation_and_pin_rendering() -> None:
    image = _validated_image(png_bytes((1200, 850)))
    product = PRODUCTS_BY_KEY["geocolour"]
    rendered = _draw_location_pin(
        image,
        latitude=48.84,
        longitude=12.40,
        location_name="Geiselhoering",
        observed_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        product=product,
    )
    output = Image.open(io.BytesIO(rendered)).convert("RGB")
    assert output.size == image.size
    red_pixels = sum(
        1
        for red, green, blue in output.getdata()
        if red > 180 and green < 80 and blue < 100
    )
    assert red_pixels > 20

    with pytest.raises(SatelliteImageError, match="valid raster"):
        _validated_image(b"not an image")
    with pytest.raises(SatelliteImageError, match="invalid size"):
        _validated_image(b"")


def test_capabilities_parser_discovers_real_product_frames(tmp_path: Path) -> None:
    service = SatelliteImageService(tmp_path)
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    parsed = service._parse_capabilities(capabilities_xml(), now)

    assert parsed["mtg_fd:rgb_geocolour"][-1].iso() == "2026-08-05T12:00:00Z"
    assert len(parsed["mtg_fd:rgb_geocolour"]) == 13
    assert parsed["mtg_fd:ir105_hrfi"][0].iso() == "2026-08-05T11:40:00Z"
    assert parsed["mtg_fd:rgb_cloudtype"][-1].iso() == "2026-08-05T11:50:00Z"

    with pytest.raises(SatelliteImageError, match="Invalid"):
        service._parse_capabilities(b"<broken", now)


@pytest.mark.asyncio
async def test_metadata_reports_products_frames_and_freshness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SatelliteImageService(tmp_path)
    now = datetime.now(UTC)
    current = now.replace(second=0, microsecond=0)
    xml = capabilities_xml().replace(
        b"2026-08-05T10:00:00Z/2026-08-05T12:00:00Z/PT10M",
        (
            (current - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
            + "/"
            + current.isoformat().replace("+00:00", "Z")
            + "/PT10M"
        ).encode(),
    )

    async def fake_capabilities() -> tuple[bytes, datetime, bool]:
        return xml, now, False

    monkeypatch.setattr(service, "_capabilities", fake_capabilities)
    payload = await service.metadata("geocolour")

    assert payload["provider"] == "EUMETSAT"
    assert payload["satellite"] == "Meteosat-12 / MTG-I1 / FCI"
    assert payload["selectedProduct"]["key"] == "geocolour"
    assert payload["frames"][-1] == current.isoformat().replace("+00:00", "Z")
    geocolour = next(
        item for item in payload["products"] if item["key"] == "geocolour"
    )
    assert geocolour["available"] is True
    assert geocolour["fresh"] is True
    assert payload["freshnessLimitMinutes"] == int(
        FRESHNESS_LIMIT.total_seconds() / 60
    )

    with pytest.raises(SatelliteImageError, match="Unknown"):
        await service.metadata("does-not-exist")


@pytest.mark.asyncio
async def test_render_uses_selected_product_frame_and_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SatelliteImageService(tmp_path)
    observed = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)

    async def fake_frame_image(
        product: SatelliteProduct,
        frame: SatelliteFrame,
    ) -> tuple[Image.Image, bool]:
        assert product.key == "infrared"
        assert frame.observed_at == observed
        return Image.new("RGB", (120, 80), (20, 30, 40)), False

    monkeypatch.setattr(service, "_frame_image", fake_frame_image)
    result = await service.render(
        product_key="infrared",
        observed_at=observed,
        latitude=48.84,
        longitude=12.40,
        location_name="Geiselhoering",
    )

    assert result.product.key == "infrared"
    assert result.observed_at == observed
    assert result.cached is False
    assert Image.open(io.BytesIO(result.png)).size == (120, 80)


@pytest.mark.asyncio
async def test_capabilities_memory_download_and_fallback_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SatelliteImageService(tmp_path)
    content = capabilities_xml()
    service._capabilities_memory = content
    service._capabilities_fetched_at = datetime.now(UTC)
    memory, _, fallback = await service._capabilities()
    assert memory == content
    assert fallback is False

    service._capabilities_memory = None
    service._capabilities_fetched_at = None

    async def fake_download() -> bytes:
        return content

    monkeypatch.setattr(service, "_download_capabilities", fake_download)
    downloaded, _, fallback = await service._capabilities()
    assert downloaded == content
    assert fallback is False
    assert service._capabilities_path.exists()

    service._capabilities_memory = None
    service._capabilities_fetched_at = None

    async def fail_download() -> bytes:
        raise SatelliteImageError("offline")

    monkeypatch.setattr(service, "_download_capabilities", fail_download)
    cached, _, fallback = await service._capabilities()
    assert cached == content
    assert fallback is True

    old = datetime.now(UTC) - CAPABILITIES_FALLBACK_AGE - timedelta(minutes=1)
    os.utime(service._capabilities_path, (old.timestamp(), old.timestamp()))
    service._capabilities_memory = None
    service._capabilities_fetched_at = None
    with pytest.raises(SatelliteImageError, match="offline"):
        await service._capabilities()


@pytest.mark.asyncio
async def test_frame_cache_hit_and_download_miss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SatelliteImageService(tmp_path)
    product = PRODUCTS_BY_KEY["geocolour"]
    frame = SatelliteFrame(datetime(2026, 8, 5, 12, 0, tzinfo=UTC))
    path = service._frame_cache_path(product, frame)
    service._write_frame_cache(path, Image.new("RGB", (20, 10), (1, 2, 3)))

    cached, was_cached = await service._frame_image(product, frame)
    assert cached.size == (20, 10)
    assert was_cached is True

    path.unlink()

    async def fake_download(
        selected: SatelliteProduct,
        selected_frame: SatelliteFrame,
    ) -> Image.Image:
        assert selected is product
        assert selected_frame == frame
        return Image.new("RGB", (30, 15), (4, 5, 6))

    monkeypatch.setattr(service, "_download_frame", fake_download)
    downloaded, was_cached = await service._frame_image(product, frame)
    assert downloaded.size == (30, 15)
    assert was_cached is False
    assert path.exists()


def test_invalid_or_expired_cache_files_are_ignored(tmp_path: Path) -> None:
    service = SatelliteImageService(tmp_path)
    product = PRODUCTS_BY_KEY["geocolour"]
    frame = SatelliteFrame(datetime(2026, 8, 5, 12, 0, tzinfo=UTC))
    path = service._frame_cache_path(product, frame)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"broken")
    assert service._read_frame_cache(path) is None

    service._capabilities_path.parent.mkdir(parents=True, exist_ok=True)
    service._capabilities_path.write_bytes(b"")
    assert service._read_capabilities_cache(datetime.now(UTC)) is None


class FakeResponse:
    def __init__(
        self,
        *,
        content: bytes,
        content_type: str = "image/png",
        error: Exception | None = None,
    ) -> None:
        self.content = content
        self.headers = {"content-type": content_type}
        self._error = error

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error


class FakeAsyncClient:
    response = FakeResponse(content=b"")
    last_url = ""
    last_params: ClassVar[dict[str, str]] = {}

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> FakeResponse:
        del headers
        type(self).last_url = url
        type(self).last_params = params
        return type(self).response


@pytest.mark.asyncio
async def test_http_downloads_validate_capabilities_and_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SatelliteImageService(tmp_path)
    monkeypatch.setattr(
        "nowcast_service.satellite_image.httpx.AsyncClient",
        FakeAsyncClient,
    )
    FakeAsyncClient.response = FakeResponse(
        content=capabilities_xml(),
        content_type="text/xml",
    )
    assert await service._download_capabilities() == capabilities_xml()
    assert FakeAsyncClient.last_url == EUMETVIEW_WMS_URL
    assert FakeAsyncClient.last_params["request"] == "GetCapabilities"

    frame = SatelliteFrame(datetime(2026, 8, 5, 12, 0, tzinfo=UTC))
    FakeAsyncClient.response = FakeResponse(content=png_bytes())
    image = await service._download_frame(PRODUCTS_BY_KEY["infrared"], frame)
    assert image.size == (120, 80)
    assert FakeAsyncClient.last_params["time"] == frame.iso()
    assert FakeAsyncClient.last_params["layers"] == "mtg_fd:ir105_hrfi"

    FakeAsyncClient.response = FakeResponse(
        content=b"error",
        content_type="text/plain",
    )
    with pytest.raises(SatelliteImageError, match="returned"):
        await service._download_frame(PRODUCTS_BY_KEY["infrared"], frame)

    request = httpx.Request("GET", EUMETVIEW_WMS_URL)
    response = httpx.Response(503, request=request)
    FakeAsyncClient.response = FakeResponse(
        content=b"",
        error=httpx.HTTPStatusError(
            "unavailable",
            request=request,
            response=response,
        ),
    )
    with pytest.raises(SatelliteImageError, match="capabilities unavailable"):
        await service._download_capabilities()
