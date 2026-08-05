"""Server-side DWD METEOSAT image rendering for the local dashboard."""

from __future__ import annotations

import asyncio
import io
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import httpx
import truststore
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

BAVARIA_BBOX: Final[tuple[float, float, float, float]] = (8.75, 47.0, 14.05, 50.75)
IMAGE_WIDTH: Final = 960
IMAGE_HEIGHT: Final = 680
CACHE_MAX_AGE: Final = timedelta(minutes=20)
STALE_CACHE_MAX_AGE: Final = timedelta(hours=12)
MAX_IMAGE_BYTES: Final = 12 * 1024 * 1024
DWD_WMS_URL: Final = "https://maps.dwd.de/geoserver/dwd/wms"
DWD_SATELLITE_LAYERS: Final[tuple[str, ...]] = (
    "dwd:Satellite_meteosat_1km_euat_rgb_day_hrv_and_night_ir108_3h",
    "dwd:Satellite_meteosat_1km_euat_rgb_clouds_day_and_night",
    "dwd:SAT_EU_RGB",
)


class SatelliteImageError(RuntimeError):
    """Raised when no genuine DWD satellite image can be provided."""


@dataclass(frozen=True)
class SatelliteImageResult:
    """Rendered image plus provenance information for response headers."""

    png: bytes
    fetched_at: datetime
    layer: str
    stale_cache: bool


def location_to_pixel(
    *,
    latitude: float,
    longitude: float,
    bbox: tuple[float, float, float, float] = BAVARIA_BBOX,
    width: int = IMAGE_WIDTH,
    height: int = IMAGE_HEIGHT,
) -> tuple[int, int]:
    """Project WGS84 coordinates into a WMS EPSG:4326 raster."""

    minimum_longitude, minimum_latitude, maximum_longitude, maximum_latitude = bbox
    if not minimum_longitude <= longitude <= maximum_longitude:
        raise ValueError("longitude is outside the Bavaria satellite image")
    if not minimum_latitude <= latitude <= maximum_latitude:
        raise ValueError("latitude is outside the Bavaria satellite image")
    x = round(
        (longitude - minimum_longitude)
        / (maximum_longitude - minimum_longitude)
        * (width - 1)
    )
    y = round(
        (maximum_latitude - latitude)
        / (maximum_latitude - minimum_latitude)
        * (height - 1)
    )
    return x, y


def _validated_image(content: bytes) -> Image.Image:
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise SatelliteImageError("DWD satellite image has an invalid size")
    try:
        with Image.open(io.BytesIO(content)) as candidate:
            candidate.verify()
        with Image.open(io.BytesIO(content)) as candidate:
            return candidate.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise SatelliteImageError("DWD response is not a valid raster image") from exc


def _draw_location_pin(
    image: Image.Image,
    *,
    latitude: float,
    longitude: float,
    location_name: str,
    fetched_at: datetime,
) -> bytes:
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = ImageFont.load_default(size=18)
    small_font = ImageFont.load_default(size=15)
    x, y = location_to_pixel(
        latitude=latitude,
        longitude=longitude,
        width=canvas.width,
        height=canvas.height,
    )

    pin_radius = 14
    pin_tip = 22
    draw.polygon(
        [(x - 9, y + 8), (x + 9, y + 8), (x, y + pin_tip)],
        fill=(211, 28, 45, 255),
        outline=(255, 255, 255, 255),
        width=2,
    )
    draw.ellipse(
        (x - pin_radius, y - pin_radius, x + pin_radius, y + pin_radius),
        fill=(211, 28, 45, 255),
        outline=(255, 255, 255, 255),
        width=3,
    )
    draw.ellipse(
        (x - 4, y - 4, x + 4, y + 4),
        fill=(255, 255, 255, 255),
    )

    safe_name = location_name.strip()[:80] or "Ausgewählter Ort"
    label_box = draw.textbbox((0, 0), safe_name, font=font)
    label_width = label_box[2] - label_box[0] + 20
    label_height = label_box[3] - label_box[1] + 14
    label_x = min(max(8, x + 20), canvas.width - label_width - 8)
    label_y = min(max(8, y - label_height // 2), canvas.height - label_height - 8)
    draw.rounded_rectangle(
        (label_x, label_y, label_x + label_width, label_y + label_height),
        radius=8,
        fill=(5, 10, 15, 220),
        outline=(255, 255, 255, 210),
        width=1,
    )
    draw.text(
        (label_x + 10, label_y + 7),
        safe_name,
        fill=(255, 255, 255, 255),
        font=font,
    )

    provenance = (
        "Echtes DWD-METEOSAT-Satellitenbild · Bayernausschnitt · "
        f"abgerufen {fetched_at.astimezone(UTC):%d.%m.%Y %H:%M UTC}"
    )
    provenance_box = draw.textbbox((0, 0), provenance, font=small_font)
    provenance_width = provenance_box[2] - provenance_box[0] + 20
    provenance_height = provenance_box[3] - provenance_box[1] + 12
    draw.rounded_rectangle(
        (
            8,
            canvas.height - provenance_height - 8,
            min(canvas.width - 8, provenance_width + 8),
            canvas.height - 8,
        ),
        radius=7,
        fill=(5, 10, 15, 205),
    )
    draw.text(
        (18, canvas.height - provenance_height - 2),
        provenance,
        fill=(238, 244, 248, 255),
        font=small_font,
    )

    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


class SatelliteImageService:
    """Fetch, validate, cache and annotate genuine DWD satellite imagery."""

    def __init__(self, data_dir: Path) -> None:
        self._cache_dir = Path(data_dir) / "cache" / "satellite"
        self._cache_path = self._cache_dir / "dwd-bavaria-latest.png"
        self._metadata_path = self._cache_dir / "dwd-bavaria-latest.txt"
        self._lock = asyncio.Lock()
        self._memory_image: Image.Image | None = None
        self._memory_fetched_at: datetime | None = None
        self._memory_layer: str | None = None

    async def render(
        self,
        *,
        latitude: float,
        longitude: float,
        location_name: str,
    ) -> SatelliteImageResult:
        location_to_pixel(latitude=latitude, longitude=longitude)
        image, fetched_at, layer, stale = await self._base_image()
        png = await asyncio.to_thread(
            _draw_location_pin,
            image,
            latitude=latitude,
            longitude=longitude,
            location_name=location_name,
            fetched_at=fetched_at,
        )
        return SatelliteImageResult(
            png=png,
            fetched_at=fetched_at,
            layer=layer,
            stale_cache=stale,
        )

    async def _base_image(self) -> tuple[Image.Image, datetime, str, bool]:
        now = datetime.now(UTC)
        if (
            self._memory_image is not None
            and self._memory_fetched_at is not None
            and self._memory_layer is not None
            and now - self._memory_fetched_at <= CACHE_MAX_AGE
        ):
            return (
                self._memory_image.copy(),
                self._memory_fetched_at,
                self._memory_layer,
                False,
            )

        async with self._lock:
            now = datetime.now(UTC)
            cached = await asyncio.to_thread(self._read_cache, now, CACHE_MAX_AGE)
            if cached is not None:
                image, fetched_at, layer = cached
                self._remember(image, fetched_at, layer)
                return image.copy(), fetched_at, layer, False

            try:
                image, fetched_at, layer = await self._download()
            except SatelliteImageError:
                stale = await asyncio.to_thread(
                    self._read_cache,
                    now,
                    STALE_CACHE_MAX_AGE,
                )
                if stale is None:
                    raise
                image, fetched_at, layer = stale
                self._remember(image, fetched_at, layer)
                return image.copy(), fetched_at, layer, True

            await asyncio.to_thread(self._write_cache, image, fetched_at, layer)
            self._remember(image, fetched_at, layer)
            return image.copy(), fetched_at, layer, False

    def _remember(self, image: Image.Image, fetched_at: datetime, layer: str) -> None:
        self._memory_image = image.copy()
        self._memory_fetched_at = fetched_at
        self._memory_layer = layer

    async def _download(self) -> tuple[Image.Image, datetime, str]:
        tls_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        timeout = httpx.Timeout(20.0, connect=8.0)
        errors: list[str] = []
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            verify=tls_context,
            follow_redirects=True,
        ) as client:
            for layer in DWD_SATELLITE_LAYERS:
                try:
                    response = await client.get(
                        DWD_WMS_URL,
                        params={
                            "service": "WMS",
                            "version": "1.1.1",
                            "request": "GetMap",
                            "layers": layer,
                            "styles": "",
                            "bbox": ",".join(str(value) for value in BAVARIA_BBOX),
                            "width": str(IMAGE_WIDTH),
                            "height": str(IMAGE_HEIGHT),
                            "srs": "EPSG:4326",
                            "format": "image/png",
                            "transparent": "false",
                        },
                        headers={"User-Agent": "Astro-Wolkencheck/4.3"},
                    )
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    if not content_type.startswith("image/"):
                        raise SatelliteImageError(
                            f"DWD layer {layer} returned {content_type or 'no type'}"
                        )
                    image = await asyncio.to_thread(_validated_image, response.content)
                    return image, datetime.now(UTC), layer
                except (httpx.HTTPError, SatelliteImageError) as exc:
                    errors.append(f"{layer}:{type(exc).__name__}")
        raise SatelliteImageError(
            "No genuine DWD satellite layer could be loaded ("
            + ", ".join(errors)
            + ")"
        )

    def _read_cache(
        self,
        now: datetime,
        maximum_age: timedelta,
    ) -> tuple[Image.Image, datetime, str] | None:
        if not self._cache_path.exists() or not self._metadata_path.exists():
            return None
        try:
            metadata = self._metadata_path.read_text(encoding="utf-8").splitlines()
            fetched_at = datetime.fromisoformat(metadata[0]).astimezone(UTC)
            layer = metadata[1]
            if now - fetched_at > maximum_age:
                return None
            image = _validated_image(self._cache_path.read_bytes())
            return image, fetched_at, layer
        except (IndexError, OSError, ValueError, SatelliteImageError):
            return None

    def _write_cache(
        self,
        image: Image.Image,
        fetched_at: datetime,
        layer: str,
    ) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        image_output = io.BytesIO()
        image.save(image_output, format="PNG", optimize=True)
        image_temporary = self._cache_path.with_suffix(".png.tmp")
        metadata_temporary = self._metadata_path.with_suffix(".txt.tmp")
        image_temporary.write_bytes(image_output.getvalue())
        metadata_temporary.write_text(
            f"{fetched_at.isoformat()}\n{layer}\n",
            encoding="utf-8",
            newline="\n",
        )
        image_temporary.replace(self._cache_path)
        metadata_temporary.replace(self._metadata_path)
