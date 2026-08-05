"""EUMETSAT MTG satellite imagery for the local dashboard."""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
import re
import ssl
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import httpx
import truststore
from defusedxml import ElementTree
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

BAVARIA_BBOX: Final[tuple[float, float, float, float]] = (8.75, 47.0, 14.05, 50.75)
IMAGE_WIDTH: Final = 1200
IMAGE_HEIGHT: Final = 850
MAX_IMAGE_BYTES: Final = 20 * 1024 * 1024
MAX_CAPABILITIES_BYTES: Final = 12 * 1024 * 1024
FRESHNESS_LIMIT: Final = timedelta(minutes=20)
CAPABILITIES_TTL: Final = timedelta(minutes=3)
CAPABILITIES_FALLBACK_AGE: Final = timedelta(hours=6)
FRAME_CACHE_MAX_AGE: Final = timedelta(days=2)
MAX_TIMELINE_FRAMES: Final = 24
EUMETVIEW_WMS_URL: Final = "https://view.eumetsat.int/geoserver/wms"
WMS_NAMESPACE: Final = "http://www.opengis.net/wms"


@dataclass(frozen=True)
class SatelliteProduct:
    key: str
    layer: str
    title: str
    description: str
    nominal_cadence_minutes: int


PRODUCTS: Final[tuple[SatelliteProduct, ...]] = (
    SatelliteProduct(
        key="geocolour",
        layer="mtg_fd:rgb_geocolour",
        title="GeoColour Tag/Nacht",
        description="Naturnahe MTG-FCI-Darstellung mit Tag- und Nachtkomponente.",
        nominal_cadence_minutes=10,
    ),
    SatelliteProduct(
        key="infrared",
        layer="mtg_fd:ir105_hrfi",
        title="Infrarot 10,5 µm",
        description="Hochaufgelöste Wolkenstruktur bei Tag und Nacht.",
        nominal_cadence_minutes=10,
    ),
    SatelliteProduct(
        key="cloudtype",
        layer="mtg_fd:rgb_cloudtype",
        title="Wolkentypen RGB",
        description="Unterscheidet Wolkenhöhe, optische Dicke und Wolkenphase.",
        nominal_cadence_minutes=10,
    ),
    SatelliteProduct(
        key="cloudphase",
        layer="mtg_fd:rgb_cloudphase",
        title="Wolkenphase RGB",
        description="Trennt Wasser- und Eiswolken; nur bei Tageslicht sinnvoll.",
        nominal_cadence_minutes=10,
    ),
    SatelliteProduct(
        key="lightning",
        layer="mtg_fd:li_afa",
        title="Blitzaktivität 5 Minuten",
        description="MTG Lightning Imager, akkumulierte Blitzfläche.",
        nominal_cadence_minutes=5,
    ),
)
PRODUCTS_BY_KEY: Final = {product.key: product for product in PRODUCTS}
_DURATION_PATTERN: Final = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


class SatelliteImageError(RuntimeError):
    """Raised when genuine EUMETSAT imagery cannot be provided."""


@dataclass(frozen=True)
class SatelliteFrame:
    observed_at: datetime

    def iso(self) -> str:
        return self.observed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SatelliteImageResult:
    """Rendered image plus provenance information for response headers."""

    png: bytes
    observed_at: datetime
    product: SatelliteProduct
    cached: bool


def _parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_duration(value: str) -> timedelta:
    match = _DURATION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"Unsupported ISO duration: {value}")
    return timedelta(
        days=int(match.group("days") or 0),
        hours=int(match.group("hours") or 0),
        minutes=int(match.group("minutes") or 0),
        seconds=float(match.group("seconds") or 0),
    )


def _expand_interval(
    start: datetime,
    end: datetime,
    step: timedelta,
    *,
    now: datetime,
    limit: int,
) -> list[datetime]:
    if step <= timedelta(0):
        raise ValueError("Satellite time step must be positive")
    effective_end = min(end, now + timedelta(minutes=2))
    if effective_end < start:
        return []
    count = math.floor((effective_end - start) / step)
    latest = start + count * step
    result: list[datetime] = []
    current = latest
    while current >= start and len(result) < limit:
        result.append(current)
        current -= step
    result.reverse()
    return result


def parse_time_dimension(
    raw: str,
    *,
    now: datetime | None = None,
    limit: int = MAX_TIMELINE_FRAMES,
) -> tuple[SatelliteFrame, ...]:
    """Parse WMS time lists and ISO intervals into recent real frames."""

    reference = (now or datetime.now(UTC)).astimezone(UTC)
    values: list[datetime] = []
    for item in raw.replace("\n", "").split(","):
        entry = item.strip()
        if not entry:
            continue
        parts = entry.split("/")
        try:
            if len(parts) == 3:
                values.extend(
                    _expand_interval(
                        _parse_datetime(parts[0]),
                        _parse_datetime(parts[1]),
                        _parse_duration(parts[2]),
                        now=reference,
                        limit=limit,
                    )
                )
            elif len(parts) == 1:
                timestamp = _parse_datetime(entry)
                if timestamp <= reference + timedelta(minutes=2):
                    values.append(timestamp)
        except ValueError:
            continue
    unique = sorted(set(values))[-limit:]
    return tuple(SatelliteFrame(observed_at=value) for value in unique)


def location_to_pixel(
    *,
    latitude: float,
    longitude: float,
    bbox: tuple[float, float, float, float] = BAVARIA_BBOX,
    width: int = IMAGE_WIDTH,
    height: int = IMAGE_HEIGHT,
) -> tuple[int, int]:
    """Project WGS84 coordinates into the Bavaria raster."""

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
        raise SatelliteImageError("EUMETSAT satellite image has an invalid size")
    try:
        with Image.open(io.BytesIO(content)) as candidate:
            candidate.verify()
        with Image.open(io.BytesIO(content)) as candidate:
            return candidate.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise SatelliteImageError("EUMETSAT response is not a valid raster image") from exc


def _draw_location_pin(
    image: Image.Image,
    *,
    latitude: float,
    longitude: float,
    location_name: str,
    observed_at: datetime,
    product: SatelliteProduct,
) -> bytes:
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = ImageFont.load_default(size=21)
    small_font = ImageFont.load_default(size=17)
    x, y = location_to_pixel(
        latitude=latitude,
        longitude=longitude,
        width=canvas.width,
        height=canvas.height,
    )

    draw.polygon(
        [(x - 11, y + 9), (x + 11, y + 9), (x, y + 28)],
        fill=(220, 24, 45, 255),
        outline=(255, 255, 255, 255),
        width=3,
    )
    draw.ellipse(
        (x - 17, y - 17, x + 17, y + 17),
        fill=(220, 24, 45, 255),
        outline=(255, 255, 255, 255),
        width=4,
    )
    draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(255, 255, 255, 255))

    safe_name = location_name.strip()[:80] or "Ausgewählter Ort"
    label_box = draw.textbbox((0, 0), safe_name, font=font)
    label_width = label_box[2] - label_box[0] + 24
    label_height = label_box[3] - label_box[1] + 18
    label_x = min(max(10, x + 24), canvas.width - label_width - 10)
    label_y = min(max(10, y - label_height // 2), canvas.height - label_height - 10)
    draw.rounded_rectangle(
        (label_x, label_y, label_x + label_width, label_y + label_height),
        radius=9,
        fill=(5, 10, 15, 226),
        outline=(255, 255, 255, 220),
        width=2,
    )
    draw.text(
        (label_x + 12, label_y + 9),
        safe_name,
        fill=(255, 255, 255, 255),
        font=font,
    )

    provenance = (
        f"EUMETSAT Meteosat-12 / MTG-FCI · {product.title} · "
        f"Aufnahme {observed_at.astimezone(UTC):%d.%m.%Y %H:%M UTC}"
    )
    box = draw.textbbox((0, 0), provenance, font=small_font)
    width = min(canvas.width - 20, box[2] - box[0] + 24)
    height = box[3] - box[1] + 16
    draw.rounded_rectangle(
        (10, canvas.height - height - 10, 10 + width, canvas.height - 10),
        radius=8,
        fill=(5, 10, 15, 218),
    )
    draw.text(
        (22, canvas.height - height - 2),
        provenance,
        fill=(238, 244, 248, 255),
        font=small_font,
    )

    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _wms_bbox() -> str:
    minimum_longitude, minimum_latitude, maximum_longitude, maximum_latitude = (
        BAVARIA_BBOX
    )
    return ",".join(
        str(value)
        for value in (
            minimum_latitude,
            minimum_longitude,
            maximum_latitude,
            maximum_longitude,
        )
    )


class SatelliteImageService:
    """Discover, fetch, cache and annotate genuine EUMETSAT MTG imagery."""

    def __init__(self, data_dir: Path) -> None:
        self._cache_dir = Path(data_dir) / "cache" / "satellite"
        self._capabilities_path = self._cache_dir / "eumetview-capabilities.xml"
        self._lock = asyncio.Lock()
        self._capabilities_memory: bytes | None = None
        self._capabilities_fetched_at: datetime | None = None

    async def metadata(self, selected_key: str = "geocolour") -> dict[str, object]:
        selected = self.product(selected_key)
        capabilities, capabilities_time, fallback = await self._capabilities()
        frames_by_layer = await asyncio.to_thread(
            self._parse_capabilities,
            capabilities,
            datetime.now(UTC),
        )
        products: list[dict[str, object]] = []
        for product in PRODUCTS:
            frames = frames_by_layer.get(product.layer, ())
            latest = frames[-1].observed_at if frames else None
            age_minutes = (
                max(0.0, (datetime.now(UTC) - latest).total_seconds() / 60)
                if latest is not None
                else None
            )
            products.append(
                {
                    **asdict(product),
                    "available": bool(frames),
                    "latestTime": frames[-1].iso() if frames else None,
                    "latestAgeMinutes": round(age_minutes, 1)
                    if age_minutes is not None
                    else None,
                    "fresh": age_minutes is not None
                    and age_minutes <= FRESHNESS_LIMIT.total_seconds() / 60,
                    "frameCount": len(frames),
                }
            )
        selected_frames = frames_by_layer.get(selected.layer, ())
        return {
            "provider": "EUMETSAT",
            "satellite": "Meteosat-12 / MTG-I1 / FCI",
            "selectedProduct": asdict(selected),
            "products": products,
            "frames": [frame.iso() for frame in selected_frames],
            "freshnessLimitMinutes": int(FRESHNESS_LIMIT.total_seconds() / 60),
            "capabilitiesFetchedAt": capabilities_time.isoformat().replace("+00:00", "Z"),
            "capabilitiesFallback": fallback,
            "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

    async def render(
        self,
        *,
        product_key: str,
        observed_at: datetime,
        latitude: float,
        longitude: float,
        location_name: str,
    ) -> SatelliteImageResult:
        product = self.product(product_key)
        location_to_pixel(latitude=latitude, longitude=longitude)
        frame = SatelliteFrame(observed_at=observed_at.astimezone(UTC))
        image, cached = await self._frame_image(product, frame)
        png = await asyncio.to_thread(
            _draw_location_pin,
            image,
            latitude=latitude,
            longitude=longitude,
            location_name=location_name,
            observed_at=frame.observed_at,
            product=product,
        )
        return SatelliteImageResult(
            png=png,
            observed_at=frame.observed_at,
            product=product,
            cached=cached,
        )

    @staticmethod
    def product(key: str) -> SatelliteProduct:
        try:
            return PRODUCTS_BY_KEY[key]
        except KeyError as exc:
            raise SatelliteImageError(f"Unknown satellite product: {key}") from exc

    async def _capabilities(self) -> tuple[bytes, datetime, bool]:
        now = datetime.now(UTC)
        if (
            self._capabilities_memory is not None
            and self._capabilities_fetched_at is not None
            and now - self._capabilities_fetched_at <= CAPABILITIES_TTL
        ):
            return self._capabilities_memory, self._capabilities_fetched_at, False

        async with self._lock:
            now = datetime.now(UTC)
            try:
                content = await self._download_capabilities()
            except SatelliteImageError:
                cached = await asyncio.to_thread(self._read_capabilities_cache, now)
                if cached is None:
                    raise
                content, fetched_at = cached
                self._capabilities_memory = content
                self._capabilities_fetched_at = fetched_at
                return content, fetched_at, True
            await asyncio.to_thread(self._write_capabilities_cache, content, now)
            self._capabilities_memory = content
            self._capabilities_fetched_at = now
            return content, now, False

    async def _download_capabilities(self) -> bytes:
        tls_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        timeout = httpx.Timeout(25.0, connect=8.0)
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
                        "request": "GetCapabilities",
                    },
                    headers={"User-Agent": "Astro-Wolkencheck/4.3"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SatelliteImageError(
                f"EUMETView capabilities unavailable: {type(exc).__name__}"
            ) from exc
        if not response.content or len(response.content) > MAX_CAPABILITIES_BYTES:
            raise SatelliteImageError("EUMETView capabilities have an invalid size")
        return response.content

    @staticmethod
    def _parse_capabilities(
        content: bytes,
        now: datetime,
    ) -> dict[str, tuple[SatelliteFrame, ...]]:
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as exc:
            raise SatelliteImageError("Invalid EUMETView capabilities XML") from exc
        result: dict[str, tuple[SatelliteFrame, ...]] = {}
        namespace = {"wms": WMS_NAMESPACE}
        wanted = {product.layer for product in PRODUCTS}
        for layer in root.findall(".//wms:Layer", namespace):
            name = layer.findtext("wms:Name", default="", namespaces=namespace)
            if name not in wanted:
                continue
            dimension_text = ""
            for tag in ("Dimension", "Extent"):
                for dimension in layer.findall(f"wms:{tag}", namespace):
                    if dimension.attrib.get("name") == "time":
                        dimension_text = dimension.text or ""
                        break
                if dimension_text:
                    break
            result[name] = parse_time_dimension(dimension_text, now=now)
        return result

    async def _frame_image(
        self,
        product: SatelliteProduct,
        frame: SatelliteFrame,
    ) -> tuple[Image.Image, bool]:
        cache_path = self._frame_cache_path(product, frame)
        cached = await asyncio.to_thread(self._read_frame_cache, cache_path)
        if cached is not None:
            return cached, True
        image = await self._download_frame(product, frame)
        await asyncio.to_thread(self._write_frame_cache, cache_path, image)
        return image, False

    async def _download_frame(
        self,
        product: SatelliteProduct,
        frame: SatelliteFrame,
    ) -> Image.Image:
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
                        "time": frame.iso(),
                    },
                    headers={"User-Agent": "Astro-Wolkencheck/4.3"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SatelliteImageError(
                f"EUMETView frame unavailable: {type(exc).__name__}"
            ) from exc
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            raise SatelliteImageError(
                f"EUMETView returned {content_type or 'no content type'}"
            )
        return await asyncio.to_thread(_validated_image, response.content)

    def _frame_cache_path(
        self,
        product: SatelliteProduct,
        frame: SatelliteFrame,
    ) -> Path:
        digest = hashlib.sha256(
            f"{product.key}:{frame.iso()}".encode("utf-8")
        ).hexdigest()[:20]
        return self._cache_dir / "frames" / f"{product.key}-{digest}.png"

    @staticmethod
    def _read_frame_cache(path: Path) -> Image.Image | None:
        if not path.exists():
            return None
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if datetime.now(UTC) - modified > FRAME_CACHE_MAX_AGE:
                return None
            return _validated_image(path.read_bytes())
        except (OSError, SatelliteImageError):
            return None

    @staticmethod
    def _write_frame_cache(path: Path, image: Image.Image) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        temporary = path.with_suffix(".png.tmp")
        temporary.write_bytes(output.getvalue())
        temporary.replace(path)

    def _read_capabilities_cache(
        self,
        now: datetime,
    ) -> tuple[bytes, datetime] | None:
        if not self._capabilities_path.exists():
            return None
        try:
            modified = datetime.fromtimestamp(
                self._capabilities_path.stat().st_mtime,
                tz=UTC,
            )
            if now - modified > CAPABILITIES_FALLBACK_AGE:
                return None
            content = self._capabilities_path.read_bytes()
            if not content or len(content) > MAX_CAPABILITIES_BYTES:
                return None
            return content, modified
        except OSError:
            return None

    def _write_capabilities_cache(self, content: bytes, fetched_at: datetime) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._capabilities_path.with_suffix(".xml.tmp")
        temporary.write_bytes(content)
        temporary.replace(self._capabilities_path)
        timestamp = fetched_at.timestamp()
        self._capabilities_path.touch()
        self._capabilities_path.chmod(0o600)
        import os

        os.utime(self._capabilities_path, (timestamp, timestamp))
