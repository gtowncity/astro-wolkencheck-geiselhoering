"""Direct retrieval of the latest available EUMETSAT WMS satellite image."""

from __future__ import annotations

import asyncio
import io
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx
import truststore
from PIL import Image, ImageDraw

from nowcast_service.satellite_image import (
    EUMETVIEW_WMS_URL,
    FRESHNESS_LIMIT,
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    SatelliteFrame,
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

DATA_STORE_BROWSE_URL = "https://api.eumetsat.int/data/browse"
# The RGB views are generated from the corresponding raw MTG acquisition cycles.
# A candidate timestamp is never accepted from these collections on its own: the
# timestamped WMS raster must reproduce the untimed live WMS raster pixel-exactly.
DATA_STORE_COLLECTION_BY_PRODUCT = {
    "geocolour": "EO:EUM:DAT:0662",
    "infrared": "EO:EUM:DAT:0665",
    "cloudtype": "EO:EUM:DAT:0662",
    "cloudphase": "EO:EUM:DAT:0662",
    "lightning": "EO:EUM:DAT:0687",
}
MAX_OBSERVATION_CANDIDATES = 8
# For the live position, freshness is more important than preserving a delayed
# derived RGB. IR10.5 is closest to the direct FCI observation stream; GeoColour
# is the secondary fallback. Historical frames are never substituted.
LIVE_FALLBACK_PRODUCT_KEYS = ("infrared", "geocolour")


@dataclass(frozen=True)
class LatestSatelliteImageResult:
    """Latest WMS image with truthful acquisition and retrieval provenance."""

    png: bytes
    product: SatelliteProduct
    retrieved_at: datetime
    observed_at: datetime | None = None
    observation_time_source: str = "UNVERIFIED"
    requested_product: SatelliteProduct | None = None
    auto_fallback: bool = False


@dataclass(frozen=True)
class _LatestCandidate:
    image: Image.Image
    product: SatelliteProduct
    retrieved_at: datetime
    observed_at: datetime | None
    observation_time_source: str


def _candidate_age(candidate: _LatestCandidate) -> timedelta | None:
    if candidate.observed_at is None:
        return None
    return max(timedelta(0), candidate.retrieved_at - candidate.observed_at)


def _candidate_is_fresh(candidate: _LatestCandidate) -> bool:
    age = _candidate_age(candidate)
    return age is not None and age <= FRESHNESS_LIMIT


def _candidate_is_newer(candidate: _LatestCandidate, current: _LatestCandidate) -> bool:
    if candidate.observed_at is None:
        return False
    if current.observed_at is None:
        return True
    return candidate.observed_at > current.observed_at


async def _load_latest_candidate(
    *,
    service: SatelliteImageService,
    product: SatelliteProduct,
) -> _LatestCandidate:
    retrieved_at = datetime.now(UTC)
    image = await _download_latest_frame(product)
    observed_at, observation_time_source = await _resolve_observation_time(
        service=service,
        product=product,
        latest_image=image,
        reference=retrieved_at,
    )
    return _LatestCandidate(
        image=image,
        product=product,
        retrieved_at=retrieved_at,
        observed_at=observed_at,
        observation_time_source=observation_time_source,
    )


async def _select_freshest_live_candidate(
    *,
    service: SatelliteImageService,
    requested: SatelliteProduct,
) -> _LatestCandidate:
    """Prefer the requested layer, but replace a stale/unverified live image if possible."""

    best = await _load_latest_candidate(service=service, product=requested)
    if _candidate_is_fresh(best):
        return best

    for fallback_key in LIVE_FALLBACK_PRODUCT_KEYS:
        if fallback_key == requested.key:
            continue
        try:
            fallback = await _load_latest_candidate(
                service=service,
                product=service.product(fallback_key),
            )
        except SatelliteImageError:
            continue
        if _candidate_is_newer(fallback, best):
            best = fallback
        if _candidate_is_fresh(fallback):
            return fallback
    return best


async def render_latest_satellite_image(
    *,
    service: SatelliteImageService,
    product_key: str,
    latitude: float,
    longitude: float,
    location_name: str,
) -> LatestSatelliteImageResult:
    """Fetch the freshest truthful live image, falling back from delayed RGBs."""

    requested_product = service.product(product_key)
    location_to_pixel(latitude=latitude, longitude=longitude)
    candidate = await _select_freshest_live_candidate(
        service=service,
        requested=requested_product,
    )
    auto_fallback = candidate.product.key != requested_product.key
    png = await asyncio.to_thread(
        _draw_latest_location_pin,
        candidate.image,
        latitude=latitude,
        longitude=longitude,
        location_name=location_name,
        retrieved_at=candidate.retrieved_at,
        observed_at=candidate.observed_at,
        product=candidate.product,
        requested_product=requested_product if auto_fallback else None,
    )
    return LatestSatelliteImageResult(
        png=png,
        product=candidate.product,
        retrieved_at=candidate.retrieved_at,
        observed_at=candidate.observed_at,
        observation_time_source=candidate.observation_time_source,
        requested_product=requested_product,
        auto_fallback=auto_fallback,
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


async def _resolve_observation_time(
    *,
    service: SatelliteImageService,
    product: SatelliteProduct,
    latest_image: Image.Image,
    reference: datetime,
) -> tuple[datetime | None, str]:
    """Resolve a real timestamp only when its exact WMS pixels match the live image."""

    data_store_candidates = await _data_store_candidate_times(product, reference)
    matched = await _match_observation_time(
        service=service,
        product=product,
        latest_image=latest_image,
        candidates=data_store_candidates,
    )
    if matched is not None:
        return matched, "DATA_STORE_WMS_PIXEL_MATCH"

    capabilities_candidates = await _capabilities_candidate_times(
        service,
        product,
        reference,
    )
    matched = await _match_observation_time(
        service=service,
        product=product,
        latest_image=latest_image,
        candidates=capabilities_candidates,
    )
    if matched is not None:
        return matched, "WMS_CAPABILITIES_PIXEL_MATCH"
    return None, "UNVERIFIED"


async def _data_store_candidate_times(
    product: SatelliteProduct,
    reference: datetime,
) -> tuple[datetime, ...]:
    """Read recent sensing times from the unauthenticated EUMETSAT Browse API."""

    collection_id = DATA_STORE_COLLECTION_BY_PRODUCT.get(product.key)
    if collection_id is None:
        return ()
    encoded_collection = quote(collection_id, safe="")
    tls_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    timeout = httpx.Timeout(12.0, connect=5.0)
    candidates: set[datetime] = set()
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            verify=tls_context,
            follow_redirects=True,
        ) as client:
            for hour_offset in range(3):
                hour = (reference - timedelta(hours=hour_offset)).replace(
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                url = (
                    f"{DATA_STORE_BROWSE_URL}/collections/{encoded_collection}/dates/"
                    f"{hour:%Y/%m/%d}/times/{hour:%H}/products"
                )
                try:
                    response = await client.get(
                        url,
                        params={"format": "json"},
                        headers={"User-Agent": "Astro-Wolkencheck/4.3"},
                    )
                    if response.status_code == 404:
                        continue
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError):
                    continue
                candidates.update(_product_times_from_browse_payload(payload))
                if len(candidates) >= MAX_OBSERVATION_CANDIDATES:
                    break
    except httpx.HTTPError:
        return ()

    earliest = reference - timedelta(hours=3)
    latest = reference + timedelta(minutes=2)
    recent = [value for value in candidates if earliest <= value <= latest]
    return tuple(sorted(recent, reverse=True)[:MAX_OBSERVATION_CANDIDATES])


def _product_times_from_browse_payload(payload: object) -> tuple[datetime, ...]:
    """Extract sensing-start timestamps from a Data Store hour-products response."""

    if not isinstance(payload, dict):
        return ()
    raw_products = payload.get("products")
    if not isinstance(raw_products, list):
        return ()
    values: set[datetime] = set()
    for raw_product in raw_products:
        if not isinstance(raw_product, dict):
            continue
        raw_date = raw_product.get("date")
        if not isinstance(raw_date, str):
            continue
        parsed = _parse_product_time(raw_date)
        if parsed is not None:
            values.add(parsed)
    return tuple(sorted(values, reverse=True))


def _parse_product_time(value: str) -> datetime | None:
    """Parse a single instant or the start of an ISO acquisition interval."""

    start = value.split("/", 1)[0].strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(start)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def _capabilities_candidate_times(
    service: SatelliteImageService,
    product: SatelliteProduct,
    reference: datetime,
) -> tuple[datetime, ...]:
    """Use EUMETView's own advertised frame list only as a verified fallback."""

    try:
        metadata = await service.metadata(product.key)
    except SatelliteImageError:
        return ()
    raw_frames = metadata.get("frames")
    if not isinstance(raw_frames, list):
        return ()
    earliest = reference - timedelta(hours=3)
    latest = reference + timedelta(minutes=2)
    values: set[datetime] = set()
    for raw_frame in raw_frames[-MAX_OBSERVATION_CANDIDATES:]:
        if not isinstance(raw_frame, str):
            continue
        parsed = _parse_product_time(raw_frame)
        if parsed is not None and earliest <= parsed <= latest:
            values.add(parsed)
    return tuple(sorted(values, reverse=True))


async def _match_observation_time(
    *,
    service: SatelliteImageService,
    product: SatelliteProduct,
    latest_image: Image.Image,
    candidates: tuple[datetime, ...],
) -> datetime | None:
    """Accept a timestamp only if requesting it reproduces the live WMS raster exactly."""

    latest_signature = _image_signature(latest_image)
    for candidate in candidates[:MAX_OBSERVATION_CANDIDATES]:
        try:
            archived = await service._download_frame(
                product,
                SatelliteFrame(observed_at=candidate),
            )
        except SatelliteImageError:
            continue
        if _image_signature(archived) == latest_signature:
            return candidate
    return None


def _image_signature(image: Image.Image) -> tuple[tuple[int, int], str, bytes]:
    converted = image.convert("RGB")
    return converted.size, converted.mode, converted.tobytes()


def _format_retrieval_time(retrieved_at: datetime) -> str:
    """Show fetch time in local system time and UTC to avoid timezone confusion."""

    local_time = retrieved_at.astimezone()
    return f"{local_time:%H:%M} Ortszeit ({retrieved_at:%H:%M UTC})"


def _format_observation_time(observed_at: datetime) -> str:
    local_time = observed_at.astimezone()
    return (
        f"{local_time:%d.%m.%Y %H:%M} Ortszeit "
        f"({observed_at.astimezone(UTC):%H:%M UTC})"
    )


def _draw_latest_location_pin(
    image: Image.Image,
    *,
    latitude: float,
    longitude: float,
    location_name: str,
    retrieved_at: datetime,
    observed_at: datetime | None,
    product: SatelliteProduct,
    requested_product: SatelliteProduct | None = None,
) -> bytes:
    """Reuse the existing location pin and replace its footer with live provenance."""

    annotated = _draw_location_pin(
        image,
        latitude=latitude,
        longitude=longitude,
        location_name=location_name,
        observed_at=observed_at or retrieved_at,
        product=product,
    )
    with Image.open(io.BytesIO(annotated)) as opened:
        canvas = opened.convert("RGB")
    draw = ImageDraw.Draw(canvas, "RGBA")
    font, unicode_supported = _label_font(17)
    if observed_at is not None:
        timing = (
            f"Aufnahme {_format_observation_time(observed_at)} · "
            f"LIVE-Abruf {_format_retrieval_time(retrieved_at)}"
        )
    else:
        timing = (
            "Aufnahmezeit noch nicht verifiziert · "
            f"LIVE-Abruf {_format_retrieval_time(retrieved_at)}"
        )
    product_copy = product.title
    if requested_product is not None and requested_product.key != product.key:
        product_copy = f"AUTO aktuell: {requested_product.title} -> {product.title}"
    provenance = f"EUMETSAT Meteosat-12 / MTG-FCI · {product_copy} · {timing}"
    provenance = _label_text(provenance, unicode_supported=unicode_supported)
    box = draw.textbbox((0, 0), provenance, font=font)
    text_height = box[3] - box[1]
    bar_height = max(text_height + 24, 54)
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