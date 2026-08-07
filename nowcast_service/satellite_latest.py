"""Direct retrieval of the latest available EUMETSAT WMS satellite image."""

from __future__ import annotations

import asyncio
import io
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import truststore
from PIL import Image, ImageDraw

from nowcast_service.satellite_image import (
    EUMETVIEW_WMS_URL,
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    SatelliteImageError,
    SatelliteImageService,
    SatelliteProduct,
    _draw_location_pin,
    _label_font,
    _label_text,
    _validated_image,
    _wms_bbox,
    location_to_pixel,
)


@dataclass(frozen=True)
class LatestSatelliteImageResult:
    """Latest WMS image with a truthful retrieval-time provenance label."""

    png: bytes
    product: SatelliteProduct
    retrieved_at: datetime


async def render_latest_satellite_image(
    *,
    service: SatelliteImageService,
    product_key: str,
    latitude: float,
    longitude: float,
    location_name: str,
) -> LatestSatelliteImageResult:
    """Fetch the WMS latest image directly, without relying on GetCapabilities time."""

    product = service.product(product_key)
    location_to_pixel(latitude=latitude, longitude=longitude)
    retrieved_at = datetime.now(UTC)
    image = await _download_latest_frame(product)
    png = await asyncio.to_thread(
        _draw_latest_location_pin,
        image,
        latitude=latitude,
        longitude=longitude,
        location_name=location_name,
        retrieved_at=retrieved_at,
        product=product,
    )
    return LatestSatelliteImageResult(
        png=png,
        product=product,
        retrieved_at=retrieved_at,
    )


async def _download_latest_frame(product: SatelliteProduct) -> Image.Image:
    """Request EUMETView GetMap without ``time`` so WMS returns its latest image."""

    tls_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    timeout = httpx.Timeout(30.0, connect=8.0)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            verify=tls_context,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                EUMETVIEW_WMS_URL,
                params={
                    "service": "WMS",
                    "version": "1.3.0",
                    "request": "GetMap",
                    "layers": product.layer,
                    "styles": "",
                    "crs": "EPSG:4326",
                    "bbox": _wms_bbox(),
                    "width": str(IMAGE_WIDTH),
                    "height": str(IMAGE_HEIGHT),
                    "format": "image/png",
                    "transparent": "false",
                },
                headers={
                    "User-Agent": "Astro-Wolkencheck/4.3",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SatelliteImageError(
            f"EUMETView latest frame unavailable: {type(exc).__name__}"
        ) from exc
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise SatelliteImageError(
            f"EUMETView returned {content_type or 'no content type'}"
        )
    return await asyncio.to_thread(_validated_image, response.content)


def _draw_latest_location_pin(
    image: Image.Image,
    *,
    latitude: float,
    longitude: float,
    location_name: str,
    retrieved_at: datetime,
    product: SatelliteProduct,
) -> bytes:
    """Reuse the existing pin and replace its archive timestamp with live provenance."""

    annotated = _draw_location_pin(
        image,
        latitude=latitude,
        longitude=longitude,
        location_name=location_name,
        observed_at=retrieved_at,
        product=product,
    )
    with Image.open(io.BytesIO(annotated)) as opened:
        canvas = opened.convert("RGB")
    draw = ImageDraw.Draw(canvas, "RGBA")
    font, unicode_supported = _label_font(17)
    provenance = (
        f"EUMETSAT Meteosat-12 / MTG-FCI · {product.title} · "
        f"neueste verfügbare Aufnahme · direkt geladen {retrieved_at:%H:%M UTC}"
    )
    provenance = _label_text(provenance, unicode_supported=unicode_supported)
    box = draw.textbbox((0, 0), provenance, font=font)
    text_height = box[3] - box[1]
    bar_height = text_height + 24
    draw.rectangle(
        (0, canvas.height - bar_height, canvas.width, canvas.height),
        fill=(5, 10, 15, 236),
    )
    draw.text(
        (18, canvas.height - bar_height + 12),
        provenance,
        fill=(238, 244, 248, 255),
        font=font,
    )
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()
