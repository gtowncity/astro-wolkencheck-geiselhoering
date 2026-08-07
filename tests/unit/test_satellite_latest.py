import io
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from PIL import Image

from nowcast_service.satellite_image import (
    EUMETVIEW_WMS_URL,
    PRODUCTS_BY_KEY,
    SatelliteFrame,
    SatelliteImageService,
    SatelliteProduct,
)
from nowcast_service.satellite_latest import (
    DATA_STORE_BROWSE_URL,
    _data_store_candidate_times,
    _download_latest_frame,
    _format_observation_time,
    _match_observation_time,
    _product_times_from_browse_payload,
    _resolve_observation_time,
    render_latest_satellite_image,
)


def png_bytes(size: tuple[int, int] = (120, 80)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, (40, 80, 120)).save(output, format="PNG")
    return output.getvalue()


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
    last_headers: ClassVar[dict[str, str]] = {}

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
        type(self).last_url = url
        type(self).last_params = params
        type(self).last_headers = headers
        return type(self).response


@pytest.mark.asyncio
async def test_latest_download_omits_archive_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nowcast_service.satellite_latest.httpx.AsyncClient",
        FakeAsyncClient,
    )
    FakeAsyncClient.response = FakeResponse(content=png_bytes())

    image = await _download_latest_frame(PRODUCTS_BY_KEY["cloudtype"])

    assert image.size == (120, 80)
    assert FakeAsyncClient.last_url == EUMETVIEW_WMS_URL
    assert FakeAsyncClient.last_params["request"] == "GetMap"
    assert FakeAsyncClient.last_params["layers"] == "mtg_fd:rgb_cloudtype"
    assert "time" not in FakeAsyncClient.last_params
    assert FakeAsyncClient.last_headers["Cache-Control"] == "no-cache"


@pytest.mark.asyncio
async def test_latest_download_rejects_non_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nowcast_service.satellite_latest.httpx.AsyncClient",
        FakeAsyncClient,
    )
    FakeAsyncClient.response = FakeResponse(
        content=b"error",
        content_type="text/plain",
    )

    with pytest.raises(RuntimeError, match="returned"):
        await _download_latest_frame(PRODUCTS_BY_KEY["cloudtype"])


def test_product_times_from_browse_payload_supports_intervals() -> None:
    payload = {
        "products": [
            {"date": "2026-08-07T07:10:00Z/2026-08-07T07:20:00Z"},
            {"date": "2026-08-07T07:00:00+00:00"},
            {"date": "invalid"},
            {"other": "ignored"},
        ]
    }

    assert _product_times_from_browse_payload(payload) == (
        datetime(2026, 8, 7, 7, 10, tzinfo=UTC),
        datetime(2026, 8, 7, 7, 0, tzinfo=UTC),
    )
    assert _product_times_from_browse_payload([]) == ()
    assert _product_times_from_browse_payload({"products": "invalid"}) == ()


class BrowseResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", DATA_STORE_BROWSE_URL)
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                "browse failure",
                request=request,
                response=response,
            )

    def json(self) -> object:
        return self._payload


class BrowseAsyncClient:
    requested_urls: ClassVar[list[str]] = []

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> "BrowseAsyncClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> BrowseResponse:
        del params, headers
        type(self).requested_urls.append(url)
        if "/times/07/products" in url:
            return BrowseResponse(
                200,
                {
                    "products": [
                        {"date": "2026-08-07T07:10:00Z/2026-08-07T07:20:00Z"},
                        {"date": "2026-08-07T07:00:00Z/2026-08-07T07:10:00Z"},
                    ]
                },
            )
        return BrowseResponse(404, {})


@pytest.mark.asyncio
async def test_data_store_candidates_use_recent_hour_products(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    BrowseAsyncClient.requested_urls = []
    monkeypatch.setattr(
        "nowcast_service.satellite_latest.httpx.AsyncClient",
        BrowseAsyncClient,
    )
    reference = datetime(2026, 8, 7, 7, 16, tzinfo=UTC)

    candidates = await _data_store_candidate_times(
        PRODUCTS_BY_KEY["cloudtype"],
        reference,
    )

    assert candidates == (
        datetime(2026, 8, 7, 7, 10, tzinfo=UTC),
        datetime(2026, 8, 7, 7, 0, tzinfo=UTC),
    )
    assert any("EO%3AEUM%3ADAT%3A1022" in url for url in BrowseAsyncClient.requested_urls)


@pytest.mark.asyncio
async def test_pixel_match_returns_exact_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SatelliteImageService(tmp_path)
    product = PRODUCTS_BY_KEY["cloudtype"]
    newest = datetime(2026, 8, 7, 7, 10, tzinfo=UTC)
    older = datetime(2026, 8, 7, 7, 0, tzinfo=UTC)
    latest_image = Image.new("RGB", (120, 80), (20, 30, 40))

    async def fake_archive(
        selected: SatelliteProduct,
        frame: SatelliteFrame,
    ) -> Image.Image:
        assert selected is product
        if frame.observed_at == older:
            return latest_image.copy()
        return Image.new("RGB", (120, 80), (90, 80, 70))

    monkeypatch.setattr(service, "_download_frame", fake_archive)

    assert await _match_observation_time(
        service=service,
        product=product,
        latest_image=latest_image,
        candidates=(newest, older),
    ) == older


@pytest.mark.asyncio
async def test_resolver_falls_back_to_capabilities_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SatelliteImageService(tmp_path)
    product = PRODUCTS_BY_KEY["cloudtype"]
    observed = datetime(2026, 8, 7, 7, 10, tzinfo=UTC)
    image = Image.new("RGB", (120, 80), (20, 30, 40))

    async def no_data_store(*_: object) -> tuple[datetime, ...]:
        return ()

    async def capability_times(*_: object) -> tuple[datetime, ...]:
        return (observed,)

    async def match(**kwargs: object) -> datetime | None:
        candidates = kwargs["candidates"]
        assert isinstance(candidates, tuple)
        return candidates[0] if candidates else None

    monkeypatch.setattr(
        "nowcast_service.satellite_latest._data_store_candidate_times",
        no_data_store,
    )
    monkeypatch.setattr(
        "nowcast_service.satellite_latest._capabilities_candidate_times",
        capability_times,
    )
    monkeypatch.setattr(
        "nowcast_service.satellite_latest._match_observation_time",
        match,
    )

    assert await _resolve_observation_time(
        service=service,
        product=product,
        latest_image=image,
        reference=datetime(2026, 8, 7, 7, 16, tzinfo=UTC),
    ) == (observed, "WMS_CAPABILITIES_PIXEL_MATCH")


@pytest.mark.asyncio
async def test_render_latest_adds_verified_acquisition_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = datetime(2026, 8, 7, 7, 10, tzinfo=UTC)

    async def fake_download(_: object) -> Image.Image:
        return Image.new("RGB", (1200, 850), (20, 30, 40))

    async def fake_resolve(**_: object) -> tuple[datetime, str]:
        return observed, "DATA_STORE_WMS_PIXEL_MATCH"

    monkeypatch.setattr(
        "nowcast_service.satellite_latest._download_latest_frame",
        fake_download,
    )
    monkeypatch.setattr(
        "nowcast_service.satellite_latest._resolve_observation_time",
        fake_resolve,
    )
    service = SatelliteImageService(tmp_path)
    result = await render_latest_satellite_image(
        service=service,
        product_key="cloudtype",
        latitude=48.84,
        longitude=12.40,
        location_name="Geiselhöring",
    )

    with Image.open(io.BytesIO(result.png)) as image:
        rendered = image.convert("RGB")
        assert rendered.size == (1200, 850)
        red_pixels = sum(
            1
            for red, green, blue in rendered.getdata()
            if red > 180 and green < 90 and blue < 110
        )
    assert red_pixels > 20
    assert result.product.key == "cloudtype"
    assert result.observed_at == observed
    assert result.observation_time_source == "DATA_STORE_WMS_PIXEL_MATCH"
    assert "07:10 UTC" in _format_observation_time(observed)


@pytest.mark.asyncio
async def test_render_latest_does_not_invent_unverified_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_download(_: object) -> Image.Image:
        return Image.new("RGB", (1200, 850), (20, 30, 40))

    async def fake_resolve(**_: object) -> tuple[None, str]:
        return None, "UNVERIFIED"

    monkeypatch.setattr(
        "nowcast_service.satellite_latest._download_latest_frame",
        fake_download,
    )
    monkeypatch.setattr(
        "nowcast_service.satellite_latest._resolve_observation_time",
        fake_resolve,
    )
    result = await render_latest_satellite_image(
        service=SatelliteImageService(tmp_path),
        product_key="cloudtype",
        latitude=48.84,
        longitude=12.40,
        location_name="Geiselhöring",
    )

    assert result.observed_at is None
    assert result.observation_time_source == "UNVERIFIED"


@pytest.mark.asyncio
async def test_latest_http_error_is_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nowcast_service.satellite_latest.httpx.AsyncClient",
        FakeAsyncClient,
    )
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

    with pytest.raises(RuntimeError, match="latest frame unavailable"):
        await _download_latest_frame(PRODUCTS_BY_KEY["cloudtype"])
