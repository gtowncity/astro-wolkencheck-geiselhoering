"""Fast, high-resolution retrieval of current EUMETSAT MTG satellite imagery."""

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
    SatelliteFrame,
    SatelliteImageError,
    SatelliteImageService,
    SatelliteProduct,
    _label_font,
    _label_text,
    _validated_image,
    _wms_bbox,
    location_to_pixel,
)

DATA_STORE_BROWSE_URL = "https://api.eumetsat.int/data/browse"
# Use the collection that corresponds to the visualisation wherever one exists.
# The sensing time is still accepted only after an exact WMS pixel match.
DATA_STORE_COLLECTION_BY_PRODUCT = {
    "geocolour": "EO:EUM:DAT:0913",
    "infrared": "EO:EUM:DAT:0665",
    "cloudtype": "EO:EUM:DAT:1022",
    "cloudphase": "EO:EUM:DAT:0870",
    "lightning": "EO:EUM:DAT:0687",
}
MAX_OBSERVATION_CANDIDATES = 6
VERIFY_WIDTH = 360
VERIFY_HEIGHT = 255
DISPLAY_WIDTH = 2400
DISPLAY_HEIGHT = 1700
# Preserve a coloured RGB whenever possible. IR is the final near-real-time fallback.
COLOUR_PRODUCT_KEYS = ("cloudtype", "cloudphase", "geocolour")
LIVE_FALLBACK_PRODUCT_KEYS = ("cloudtype", "cloudphase", "geocolour", "infrared")


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


def _is_colour(product: SatelliteProduct) -> bool:
    return product.key in COLOUR_PRODUCT_KEYS


async def _wms_image(
    product: SatelliteProduct,
    *,
    width: int,
    height: int,
    observed_at: datetime | None = None,
    cache_bypass: bool = False,
) -> Image.Image:
    """Download one EUMETView WMS image at an explicit render resolution."""

    tls_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    timeout = httpx.Timeout(25.0, connect=7.0)
    params = {
        "service": "WMS",
        "version": "1.3.0",
        "request": "GetMap",
        "layers": product.layer,
        "styles": "",
        "crs": "EPSG:4326",
        "bbox": _wms_bbox(),
        "width": str(width),
        "height": str(height),
        "format": "image/jpeg",
        "bgcolor": "0x000000",
    }
    if observed_at is not None:
        params["time"] = observed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    headers = {"User-Agent": "Astro-Wolkencheck/4.3"}
    if cache_bypass:
        headers.update({"Cache-Control": "no-cache", "Pragma": "no-cache"})
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            verify=tls_context,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                EUMETVIEW_WMS_URL,
                params=params,
                headers=headers,
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


async def _download_latest_frame(product: SatelliteProduct) -> Image.Image:
    """Download a small untimed WMS image used to identify the current frame."""

    return await _wms_image(
        product,
        width=VERIFY_WIDTH,
        height=VERIFY_HEIGHT,
        cache_bypass=True,
    )


async def _download_display_frame(
    product: SatelliteProduct,
    observed_at: datetime | None,
) -> Image.Image:
    """Download the one selected live frame at the high display resolution."""

    return await _wms_image(
        product,
        width=DISPLAY_WIDTH,
        height=DISPLAY_HEIGHT,
        observed_at=observed_at,
        cache_bypass=observed_at is None,
    )


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


async def _load_fallback_candidate(
    service: SatelliteImageService,
    key: str,
) -> _LatestCandidate | None:
    try:
        return await _load_latest_candidate(service=service, product=service.product(key))
    except SatelliteImageError:
        return None


async def _select_freshest_live_candidate(
    *,
    service: SatelliteImageService,
    requested: SatelliteProduct,
) -> _LatestCandidate:
    """Keep the selected RGB if fresh, otherwise prefer a fresh coloured RGB over IR."""

    requested_candidate = await _load_latest_candidate(service=service, product=requested)
    if _candidate_is_fresh(requested_candidate):
        return requested_candidate

    fallback_keys = [
        key
        for key in LIVE_FALLBACK_PRODUCT_KEYS
        if key != requested.key
    ]
    loaded = await asyncio.gather(
        *(_load_fallback_candidate(service, key) for key in fallback_keys)
    )
    candidates = [requested_candidate, *(item for item in loaded if item is not None)]

    fresh_coloured = [
        item for item in candidates if _candidate_is_fresh(item) and _is_colour(item.product)
    ]
    if fresh_coloured:
        return max(
            fresh_coloured,
            key=lambda item: item.observed_at or datetime.min.replace(tzinfo=UTC),
        )

    fresh_any = [item for item in candidates if _candidate_is_fresh(item)]
    if fresh_any:
        return max(
            fresh_any,
            key=lambda item: item.observed_at or datetime.min.replace(tzinfo=UTC),
        )

    best = requested_candidate
    for candidate in candidates[1:]:
        if _candidate_is_newer(candidate, best):
            best = candidate
    return best


async def render_latest_satellite_image(
    *,
    service: SatelliteImageService,
    product_key: str,
    latitude: float,
    longitude: float,
    location_name: str,
) -> LatestSatelliteImageResult:
    """Select a truthful current frame, then render that one frame in HD."""

    requested_product = service.product(product_key)
    location_to_pixel(latitude=latitude, longitude=longitude)
    candidate = await _select_freshest_live_candidate(
        service=service,
        requested=requested_product,
    )
    auto_fallback = candidate.product.key != requested_product.key
    display_image = await _download_display_frame(candidate.product, candidate.observed_at)
    png = await asyncio.to_thread(
        _draw_latest_location_pin,
        display_image,
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


async def _resolve_observation_time(
    *,
    service: SatelliteImageService,
    product: SatelliteProduct,
    latest_image: Image.Image,
    reference: datetime,
) -> tuple[datetime | None, str]:
    """Resolve time by matching tiny equal-resolution WMS images instead of full frames."""

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
    timeout = httpx.Timeout(10.0, connect=4.0)
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
    """Use EUMETView's advertised frame list as a verified fallback."""

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
    """Accept a timestamp only if its small WMS raster exactly matches the live raster."""

    del service  # Kept in the signature for API/test compatibility.
    latest_signature = _image_signature(latest_image)
    for candidate in candidates[:MAX_OBSERVATION_CANDIDATES]:
        try:
            archived = await _wms_image(
                product,
                width=VERIFY_WIDTH,
                height=VERIFY_HEIGHT,
                observed_at=candidate,
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
    """Draw the pin and provenance once, avoiding a costly double HD PNG encode."""

    canvas = image.copy().convert("RGB")
    draw = ImageDraw.Draw(canvas, "RGBA")
    large_font, large_unicode = _label_font(32 if canvas.width >= 2000 else 21)
    footer_font, footer_unicode = _label_font(24 if canvas.width >= 2000 else 17)
    x, y = location_to_pixel(
        latitude=latitude,
        longitude=longitude,
        width=canvas.width,
        height=canvas.height,
    )
    scale = max(1.0, canvas.width / 1200)
    outer = round(17 * scale)
    inner = round(5 * scale)
    pointer = round(28 * scale)
    outline = max(3, round(4 * scale))
    draw.polygon(
        [
            (x - round(11 * scale), y + round(9 * scale)),
            (x + round(11 * scale), y + round(9 * scale)),
            (x, y + pointer),
        ],
        fill=(220, 24, 45, 255),
        outline=(255, 255, 255, 255),
    )
    draw.ellipse(
        (x - outer, y - outer, x + outer, y + outer),
        fill=(220, 24, 45, 255),
        outline=(255, 255, 255, 255),
        width=outline,
    )
    draw.ellipse(
        (x - inner, y - inner, x + inner, y + inner),
        fill=(255, 255, 255, 255),
    )

    safe_name = _label_text(
        location_name.strip()[:80] or "Ausgewählter Ort",
        unicode_supported=large_unicode,
    )
    label_box = draw.textbbox((0, 0), safe_name, font=large_font)
    label_width = label_box[2] - label_box[0] + round(28 * scale)
    label_height = label_box[3] - label_box[1] + round(20 * scale)
    label_x = min(
        max(round(10 * scale), x + round(24 * scale)),
        canvas.width - label_width - round(10 * scale),
    )
    label_y = min(
        max(round(10 * scale), y - label_height // 2),
        canvas.height - label_height - round(10 * scale),
    )
    draw.rounded_rectangle(
        (label_x, label_y, label_x + label_width, label_y + label_height),
        radius=round(9 * scale),
        fill=(5, 10, 15, 226),
        outline=(255, 255, 255, 220),
        width=max(2, round(2 * scale)),
    )
    draw.text(
        (label_x + round(14 * scale), label_y + round(10 * scale)),
        safe_name,
        fill=(255, 255, 255, 255),
        font=large_font,
    )

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
    provenance = _label_text(
        f"EUMETSAT Meteosat-12 / MTG-FCI · {product_copy} · {timing}",
        unicode_supported=footer_unicode,
    )
    box = draw.textbbox((0, 0), provenance, font=footer_font)
    text_height = box[3] - box[1]
    bar_height = max(text_height + round(30 * scale), round(54 * scale))
    draw.rectangle(
        (0, canvas.height - bar_height, canvas.width, canvas.height),
        fill=(5, 10, 15, 236),
    )
    draw.text(
        (round(18 * scale), canvas.height - bar_height + round(12 * scale)),
        provenance,
        fill=(238, 244, 248, 255),
        font=footer_font,
    )
    output = io.BytesIO()
    canvas.save(output, format="PNG", compress_level=1)
    return output.getvalue()
