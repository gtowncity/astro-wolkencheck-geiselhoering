"""Defensive parser and lifecycle helpers for DWD CAP 1.2 alerts."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree as SafeElementTree
from defusedxml.common import DefusedXmlException
from pyproj import Geod
from shapely.geometry import Point, Polygon
from shapely.validation import explain_validity

CAP_NAMESPACE = "urn:oasis:names:tc:emergency:cap:1.2"
CAP = f"{{{CAP_NAMESPACE}}}"
_GEOD = Geod(ellps="WGS84")


class CapParseError(RuntimeError):
    """Raised when a CAP document is unsafe, malformed or semantically invalid."""


class LocationMatch(StrEnum):
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CapReference:
    sender: str
    identifier: str
    sent: datetime


@dataclass(frozen=True, slots=True)
class CapCode:
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class CapCircle:
    latitude: float
    longitude: float
    radius_km: float

    def contains(self, longitude: float, latitude: float) -> bool:
        _, _, distance_m = _GEOD.inv(
            self.longitude,
            self.latitude,
            longitude,
            latitude,
        )
        return math.isfinite(distance_m) and distance_m <= self.radius_km * 1000


@dataclass(frozen=True, slots=True)
class CapArea:
    description: str
    polygons: tuple[tuple[tuple[float, float], ...], ...]
    circles: tuple[CapCircle, ...]
    geocodes: tuple[CapCode, ...]
    altitude: float | None
    ceiling: float | None

    @property
    def has_geometry(self) -> bool:
        return bool(self.polygons or self.circles)

    def contains(self, longitude: float, latitude: float) -> bool:
        point = Point(longitude, latitude)
        for coordinates in self.polygons:
            polygon = Polygon(coordinates)
            if polygon.covers(point):
                return True
        return any(circle.contains(longitude, latitude) for circle in self.circles)


@dataclass(frozen=True, slots=True)
class CapInfo:
    language: str
    category: tuple[str, ...]
    event: str
    response_types: tuple[str, ...]
    urgency: str
    severity: str
    certainty: str
    effective: datetime | None
    onset: datetime | None
    expires: datetime | None
    sender_name: str | None
    headline: str | None
    description: str | None
    instruction: str | None
    web: str | None
    contact: str | None
    event_codes: tuple[CapCode, ...]
    parameters: tuple[CapCode, ...]
    areas: tuple[CapArea, ...]

    def location_match(self, longitude: float, latitude: float) -> LocationMatch:
        any_geometry = False
        any_unresolved_area = False
        for area in self.areas:
            if area.has_geometry:
                any_geometry = True
                if area.contains(longitude, latitude):
                    return LocationMatch.MATCH
            else:
                any_unresolved_area = True
        if any_unresolved_area:
            return LocationMatch.UNKNOWN
        if any_geometry:
            return LocationMatch.NO_MATCH
        return LocationMatch.UNKNOWN

    def is_in_force(self, now: datetime) -> bool:
        """Apply the local policy for CAP's optional ``expires`` field.

        The DWD status ZIP is a complete current-state archive. An Actual
        Alert/Update without an explicit expiry therefore remains in force
        while it is present in a fresh complete archive and has already begun.
        A later complete archive removes or supersedes it.
        """

        _require_aware(now, "now")
        start = self.onset or self.effective
        return (start is None or start <= now) and (self.expires is None or now < self.expires)


@dataclass(frozen=True, slots=True)
class CapAlert:
    identifier: str
    sender: str
    sent: datetime
    status: str
    message_type: str
    scope: str
    source: str | None
    restriction: str | None
    addresses: tuple[str, ...]
    codes: tuple[str, ...]
    note: str | None
    references: tuple[CapReference, ...]
    incidents: tuple[str, ...]
    infos: tuple[CapInfo, ...]

    def german_info(self) -> CapInfo | None:
        for info in self.infos:
            if info.language.casefold() in {"de", "de-de"}:
                return info
        return self.infos[0] if self.infos else None

    def is_public_actual(self) -> bool:
        return self.status == "Actual" and self.scope == "Public"

    def is_displayable(self, now: datetime) -> bool:
        _require_aware(now, "now")
        if not self.is_public_actual() or self.message_type not in {"Alert", "Update"}:
            return False
        info = self.german_info()
        return info is not None and info.is_in_force(now)


@dataclass(frozen=True, slots=True)
class CapSnapshot:
    active: tuple[CapAlert, ...]
    cancelled_identifiers: tuple[str, ...]
    superseded_identifiers: tuple[str, ...]


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CapParseError(f"CAP timestamp {name} must include a timezone")


def _timestamp(value: str | None, name: str, *, required: bool) -> datetime | None:
    if value is None or not value.strip():
        if required:
            raise CapParseError(f"Required CAP timestamp is missing: {name}")
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CapParseError(f"Invalid CAP timestamp: {name}") from exc
    _require_aware(result, name)
    return result.astimezone(UTC)


def _text(parent: Element, name: str, *, required: bool = False) -> str | None:
    child = parent.find(CAP + name)
    value = child.text.strip() if child is not None and child.text else None
    if required and not value:
        raise CapParseError(f"Required CAP field is missing: {name}")
    return value


def _required_text(parent: Element, name: str) -> str:
    return cast(str, _text(parent, name, required=True))


def _required_timestamp(parent: Element, name: str) -> datetime:
    return cast(
        datetime,
        _timestamp(_text(parent, name), name, required=True),
    )


def _texts(parent: Element, name: str) -> tuple[str, ...]:
    return tuple(
        child.text.strip()
        for child in parent.findall(CAP + name)
        if child.text and child.text.strip()
    )


def _code_pairs(parent: Element, container_name: str) -> tuple[CapCode, ...]:
    return tuple(
        CapCode(
            name=_required_text(container, "valueName"),
            value=_required_text(container, "value"),
        )
        for container in parent.findall(CAP + container_name)
    )


def _float(value: str | None, name: str) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except ValueError as exc:
        raise CapParseError(f"CAP {name} is not numeric") from exc
    if not math.isfinite(result):
        raise CapParseError(f"CAP {name} is not finite")
    return result


def _required_float(value: str, name: str) -> float:
    return cast(float, _float(value, name))


def _polygon(value: str) -> tuple[tuple[float, float], ...]:
    coordinates: list[tuple[float, float]] = []
    for token in value.split():
        parts = token.split(",")
        if len(parts) != 2:
            raise CapParseError("CAP polygon coordinate is malformed")
        latitude = _required_float(parts[0], "polygon latitude")
        longitude = _required_float(parts[1], "polygon longitude")
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise CapParseError("CAP polygon coordinate is outside valid bounds")
        coordinates.append((longitude, latitude))
    if len(coordinates) < 4 or coordinates[0] != coordinates[-1]:
        raise CapParseError("CAP polygon must be a closed ring with at least four points")
    polygon = Polygon(coordinates)
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise CapParseError(f"CAP polygon is invalid: {explain_validity(polygon)}")
    return tuple(coordinates)


def _circle(value: str) -> CapCircle:
    parts = value.split()
    if len(parts) != 2:
        raise CapParseError("CAP circle is malformed")
    centre = parts[0].split(",")
    if len(centre) != 2:
        raise CapParseError("CAP circle centre is malformed")
    latitude = _required_float(centre[0], "circle latitude")
    longitude = _required_float(centre[1], "circle longitude")
    radius = _required_float(parts[1], "circle radius")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180 and radius > 0):
        raise CapParseError("CAP circle values are outside valid bounds")
    return CapCircle(latitude=latitude, longitude=longitude, radius_km=radius)


def _area(element: Element) -> CapArea:
    polygons = tuple(
        _polygon(item.text.strip())
        for item in element.findall(CAP + "polygon")
        if item.text and item.text.strip()
    )
    circles = tuple(
        _circle(item.text.strip())
        for item in element.findall(CAP + "circle")
        if item.text and item.text.strip()
    )
    return CapArea(
        description=_required_text(element, "areaDesc"),
        polygons=polygons,
        circles=circles,
        geocodes=_code_pairs(element, "geocode"),
        altitude=_float(_text(element, "altitude"), "altitude"),
        ceiling=_float(_text(element, "ceiling"), "ceiling"),
    )


def _info(element: Element) -> CapInfo:
    return CapInfo(
        language=_text(element, "language") or "und",
        category=_texts(element, "category"),
        event=_required_text(element, "event"),
        response_types=_texts(element, "responseType"),
        urgency=_required_text(element, "urgency"),
        severity=_required_text(element, "severity"),
        certainty=_required_text(element, "certainty"),
        effective=_timestamp(
            _text(element, "effective"),
            "effective",
            required=False,
        ),
        onset=_timestamp(_text(element, "onset"), "onset", required=False),
        expires=_timestamp(_text(element, "expires"), "expires", required=False),
        sender_name=_text(element, "senderName"),
        headline=_text(element, "headline"),
        description=_text(element, "description"),
        instruction=_text(element, "instruction"),
        web=_text(element, "web"),
        contact=_text(element, "contact"),
        event_codes=_code_pairs(element, "eventCode"),
        parameters=_code_pairs(element, "parameter"),
        areas=tuple(_area(area) for area in element.findall(CAP + "area")),
    )


def _references(value: str | None) -> tuple[CapReference, ...]:
    if value is None:
        return ()
    references: list[CapReference] = []
    for item in value.split():
        parts = item.split(",")
        if len(parts) != 3:
            raise CapParseError("CAP references field is malformed")
        sent = cast(
            datetime,
            _timestamp(parts[2], "reference sent", required=True),
        )
        references.append(CapReference(sender=parts[0], identifier=parts[1], sent=sent))
    return tuple(references)


def parse_cap_xml(content: bytes) -> CapAlert:
    if not content or len(content) > 2 * 1024 * 1024:
        raise CapParseError("CAP XML size is outside the safety limit")
    try:
        root = cast(Element, SafeElementTree.fromstring(content))
    except (ParseError, DefusedXmlException) as exc:
        raise CapParseError("CAP XML is malformed or unsafe") from exc
    if root.tag != CAP + "alert":
        raise CapParseError("CAP root element or namespace is unsupported")

    status = _required_text(root, "status")
    message_type = _required_text(root, "msgType")
    scope = _required_text(root, "scope")
    if status not in {"Actual", "Exercise", "System", "Test", "Draft"}:
        raise CapParseError(f"Unsupported CAP status: {status}")
    if message_type not in {"Alert", "Update", "Cancel", "Ack", "Error"}:
        raise CapParseError(f"Unsupported CAP message type: {message_type}")
    if scope not in {"Public", "Restricted", "Private"}:
        raise CapParseError(f"Unsupported CAP scope: {scope}")

    return CapAlert(
        identifier=_required_text(root, "identifier"),
        sender=_required_text(root, "sender"),
        sent=_required_timestamp(root, "sent"),
        status=status,
        message_type=message_type,
        scope=scope,
        source=_text(root, "source"),
        restriction=_text(root, "restriction"),
        addresses=_texts(root, "addresses"),
        codes=_texts(root, "code"),
        note=_text(root, "note"),
        references=_references(_text(root, "references")),
        incidents=_texts(root, "incidents"),
        infos=tuple(_info(info) for info in root.findall(CAP + "info")),
    )


def resolve_cap_snapshot(alerts: Iterable[CapAlert], *, now: datetime) -> CapSnapshot:
    _require_aware(now, "now")
    latest_by_identifier: dict[str, CapAlert] = {}
    cancelled: set[str] = set()
    superseded: set[str] = set()

    for alert in alerts:
        previous = latest_by_identifier.get(alert.identifier)
        if previous is None or alert.sent > previous.sent:
            latest_by_identifier[alert.identifier] = alert
        referenced = {reference.identifier for reference in alert.references}
        if alert.message_type == "Cancel":
            cancelled.update(referenced)
            cancelled.add(alert.identifier)
        elif alert.message_type == "Update":
            superseded.update(referenced)

    active = tuple(
        sorted(
            (
                alert
                for identifier, alert in latest_by_identifier.items()
                if identifier not in cancelled
                and identifier not in superseded
                and alert.is_displayable(now)
            ),
            key=lambda item: (item.sent, item.identifier),
            reverse=True,
        )
    )
    return CapSnapshot(
        active=active,
        cancelled_identifiers=tuple(sorted(cancelled)),
        superseded_identifiers=tuple(sorted(superseded)),
    )
