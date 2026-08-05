"""Official DWD municipality warning-area index and CAP geocode resolver."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlparse

import httpx
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry

from nowcast_service.sources.cap_parser import CapAlert, CapInfo, LocationMatch

DWD_WFS_ENDPOINT = "https://maps.dwd.de/geoserver/dwd/ows"
DWD_WARNING_AREA_LAYER = "dwd:Warngebiete_Gemeinden"
_ALLOWED_CRS_NAMES = {
    "urn:ogc:def:crs:OGC:1.3:CRS84",
    "urn:ogc:def:crs:EPSG::4326",
    "EPSG:4326",
}
_MAX_RESPONSE_BYTES = 20 * 1024 * 1024
_MAX_FEATURES = 10_000


class WarningAreaError(RuntimeError):
    """Raised when official warning-area data cannot be trusted."""


@dataclass(frozen=True, slots=True)
class WarningArea:
    feature_id: str
    warncell_id: str
    name: str
    short_name: str | None
    contact: str | None
    geometry: BaseGeometry


@dataclass(frozen=True, slots=True)
class WarningAreaIndex:
    source_sha256: str
    retrieved_at: datetime
    endpoint: str
    layer: str
    areas: tuple[WarningArea, ...]

    def __post_init__(self) -> None:
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        if self.retrieved_at.utcoffset() != UTC.utcoffset(self.retrieved_at):
            raise ValueError("retrieved_at must be expressed in UTC")
        if not self.areas:
            raise ValueError("Warning-area index must contain at least one area")

    @property
    def by_warncell_id(self) -> dict[str, WarningArea]:
        return {area.warncell_id: area for area in self.areas}

    def ids_covering(self, longitude: float, latitude: float) -> tuple[str, ...]:
        _validate_lonlat(longitude, latitude)
        point = Point(longitude, latitude)
        return tuple(
            sorted(area.warncell_id for area in self.areas if area.geometry.covers(point))
        )

    def match_ids(
        self,
        warncell_ids: tuple[str, ...],
        *,
        longitude: float,
        latitude: float,
    ) -> LocationMatch:
        _validate_lonlat(longitude, latitude)
        normalized = tuple(dict.fromkeys(item.strip() for item in warncell_ids if item.strip()))
        if not normalized:
            return LocationMatch.UNKNOWN
        by_id = self.by_warncell_id
        point = Point(longitude, latitude)
        unresolved = False
        for warncell_id in normalized:
            area = by_id.get(warncell_id)
            if area is None:
                unresolved = True
                continue
            if area.geometry.covers(point):
                return LocationMatch.MATCH
        return LocationMatch.UNKNOWN if unresolved else LocationMatch.NO_MATCH

    def match(
        self,
        *,
        alert: CapAlert,
        info: CapInfo,
        longitude: float,
        latitude: float,
    ) -> LocationMatch:
        del alert
        warncell_ids = tuple(
            code.value
            for area in info.areas
            for code in area.geocodes
            if code.name.casefold() in {"warncellid", "warngemeinde"}
        )
        return self.match_ids(
            warncell_ids,
            longitude=longitude,
            latitude=latitude,
        )


def _validate_lonlat(longitude: float, latitude: float) -> None:
    if not (math.isfinite(longitude) and math.isfinite(latitude)):
        raise ValueError("Location coordinates must be finite")
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise ValueError("Location coordinates are outside valid bounds")


def _validate_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "maps.dwd.de"
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        raise WarningAreaError("Warning-area endpoint is not on the approved DWD WFS host")


def _crs_name(payload: dict[str, Any]) -> str | None:
    crs = payload.get("crs")
    if crs is None:
        return None
    if not isinstance(crs, dict):
        raise WarningAreaError("GeoJSON CRS field is malformed")
    properties = crs.get("properties")
    if not isinstance(properties, dict):
        raise WarningAreaError("GeoJSON CRS properties are malformed")
    name = properties.get("name")
    return name if isinstance(name, str) else None


def _text_property(properties: dict[str, Any], name: str, *, required: bool) -> str | None:
    value = properties.get(name)
    if value is None:
        if required:
            raise WarningAreaError(f"Warning-area property is missing: {name}")
        return None
    if not isinstance(value, str) or not value.strip():
        raise WarningAreaError(f"Warning-area property is invalid: {name}")
    return value.strip()


def _geometry(feature: dict[str, Any]) -> BaseGeometry:
    geometry_data = feature.get("geometry")
    if not isinstance(geometry_data, dict):
        raise WarningAreaError("Warning-area feature has no GeoJSON geometry")
    try:
        geometry = shape(geometry_data)
    except (TypeError, ValueError) as exc:
        raise WarningAreaError("Warning-area geometry could not be decoded") from exc
    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise WarningAreaError(f"Unsupported warning-area geometry: {geometry.geom_type}")
    if geometry.is_empty or not geometry.is_valid:
        raise WarningAreaError("Warning-area geometry is empty or invalid")
    minimum_x, minimum_y, maximum_x, maximum_y = geometry.bounds
    if not (
        -180 <= minimum_x <= maximum_x <= 180
        and -90 <= minimum_y <= maximum_y <= 90
    ):
        raise WarningAreaError("Warning-area geometry is outside longitude/latitude bounds")
    return geometry


def parse_warning_area_geojson(
    content: bytes,
    *,
    retrieved_at: datetime,
    endpoint: str = DWD_WFS_ENDPOINT,
    layer: str = DWD_WARNING_AREA_LAYER,
) -> WarningAreaIndex:
    _validate_endpoint(endpoint)
    if not content or len(content) > _MAX_RESPONSE_BYTES:
        raise WarningAreaError("Warning-area response size is outside the safety limit")
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WarningAreaError("Warning-area response is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise WarningAreaError("Warning-area response is not a GeoJSON FeatureCollection")
    crs_name = _crs_name(payload)
    if crs_name is not None and crs_name not in _ALLOWED_CRS_NAMES:
        raise WarningAreaError(f"Unsupported warning-area CRS: {crs_name}")
    features = payload.get("features")
    if not isinstance(features, list) or not features or len(features) > _MAX_FEATURES:
        raise WarningAreaError("Warning-area feature count is outside the safety limit")

    areas: list[WarningArea] = []
    seen_ids: set[str] = set()
    for raw_feature in features:
        if not isinstance(raw_feature, dict) or raw_feature.get("type") != "Feature":
            raise WarningAreaError("Warning-area entry is not a GeoJSON Feature")
        feature_id = raw_feature.get("id")
        if not isinstance(feature_id, str) or not feature_id:
            raise WarningAreaError("Warning-area feature ID is missing")
        properties = raw_feature.get("properties")
        if not isinstance(properties, dict):
            raise WarningAreaError("Warning-area properties are missing")
        warncell_id = cast(str, _text_property(properties, "WARNCELLID", required=True))
        name = cast(str, _text_property(properties, "NAME", required=True))
        if warncell_id in seen_ids:
            raise WarningAreaError(f"Duplicate warning-cell ID: {warncell_id}")
        seen_ids.add(warncell_id)
        areas.append(
            WarningArea(
                feature_id=feature_id,
                warncell_id=warncell_id,
                name=name,
                short_name=_text_property(properties, "SHORTNAME", required=False),
                contact=_text_property(properties, "CONTACT", required=False),
                geometry=_geometry(raw_feature),
            )
        )

    return WarningAreaIndex(
        source_sha256=hashlib.sha256(content).hexdigest(),
        retrieved_at=retrieved_at,
        endpoint=endpoint,
        layer=layer,
        areas=tuple(areas),
    )


class DwdWarningAreaClient:
    """Fetch a small official WFS bbox around one configured location."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch(
        self,
        *,
        longitude: float,
        latitude: float,
        retrieved_at: datetime,
        bbox_margin_degrees: float = 0.02,
    ) -> WarningAreaIndex:
        _validate_lonlat(longitude, latitude)
        if not 0 < bbox_margin_degrees <= 1:
            raise ValueError("bbox_margin_degrees must be in the interval (0, 1]")
        _validate_endpoint(DWD_WFS_ENDPOINT)
        bbox = (
            f"{longitude - bbox_margin_degrees},{latitude - bbox_margin_degrees},"
            f"{longitude + bbox_margin_degrees},{latitude + bbox_margin_degrees},EPSG:4326"
        )
        response = await self._client.get(
            DWD_WFS_ENDPOINT,
            params={
                "service": "WFS",
                "version": "1.0.0",
                "request": "GetFeature",
                "typeName": DWD_WARNING_AREA_LAYER,
                "srsName": "EPSG:4326",
                "outputFormat": "application/json",
                "bbox": bbox,
            },
            headers={"Accept": "application/json", "User-Agent": "AstroWolkencheck/4.3"},
            follow_redirects=False,
        )
        if response.status_code != 200:
            raise WarningAreaError(
                f"DWD warning-area WFS returned HTTP {response.status_code}"
            )
        if "json" not in response.headers.get("content-type", "").casefold():
            raise WarningAreaError("DWD warning-area WFS did not return JSON")
        index = parse_warning_area_geojson(
            response.content,
            retrieved_at=retrieved_at,
        )
        if not index.ids_covering(longitude, latitude):
            raise WarningAreaError("No official DWD municipality warning area covers the site")
        return index
