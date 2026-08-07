import io
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from PIL import Image

from nowcast_service.satellite_image import (
    EUMETVIEW_WMS_URL,
    PRODUCTS_BY_KEY,
    SatelliteImageService,
)
from nowcast_service.satellite_latest import (
    _download_latest_frame,
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


@pytest.mark.asyncio
async def test_render_latest_adds_pin_without_fabricating_observation_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_download(_: object) -> Image.Image:
        return Image.new("RGB", (1200, 850), (20, 30, 40))

    monkeypatch.setattr(
        "nowcast_service.satellite_latest._download_latest_frame",
        fake_download,
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
