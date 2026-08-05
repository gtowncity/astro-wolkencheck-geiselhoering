"""Complete CAP archive loading, location resolution and hazard evidence."""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from nowcast_service.decision_engine import Evidence, RiskState
from nowcast_service.downloads.safe_zip import UnsafeZipError, validate_zip
from nowcast_service.sources.cap_parser import (
    CapAlert,
    CapInfo,
    CapSnapshot,
    LocationMatch,
    parse_cap_xml,
    resolve_cap_snapshot,
)


class CapArchiveError(RuntimeError):
    """Raised when a complete CAP status archive cannot be trusted."""


class WarningAreaResolver(Protocol):
    """Resolve CAP areas that have official geocodes but no inline geometry."""

    def match(
        self,
        *,
        alert: CapAlert,
        info: CapInfo,
        longitude: float,
        latitude: float,
    ) -> LocationMatch: ...


@dataclass(frozen=True, slots=True)
class CapLocationResult:
    matched: tuple[CapAlert, ...]
    nonmatching: tuple[CapAlert, ...]
    unresolved: tuple[CapAlert, ...]

    @property
    def complete(self) -> bool:
        return not self.unresolved


@dataclass(frozen=True, slots=True)
class CapArchiveResult:
    source_file: str
    source_sha256: str
    archive_entries: int
    parsed_alerts: int
    snapshot: CapSnapshot
    location: CapLocationResult


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_location(
    snapshot: CapSnapshot,
    *,
    longitude: float,
    latitude: float,
    resolver: WarningAreaResolver | None,
) -> CapLocationResult:
    matched: list[CapAlert] = []
    nonmatching: list[CapAlert] = []
    unresolved: list[CapAlert] = []

    for alert in snapshot.active:
        info = alert.german_info()
        if info is None:
            unresolved.append(alert)
            continue
        direct = info.location_match(longitude, latitude)
        if direct is LocationMatch.MATCH:
            matched.append(alert)
            continue
        if direct is LocationMatch.NO_MATCH:
            nonmatching.append(alert)
            continue
        fallback = (
            resolver.match(
                alert=alert,
                info=info,
                longitude=longitude,
                latitude=latitude,
            )
            if resolver is not None
            else LocationMatch.UNKNOWN
        )
        if fallback is LocationMatch.MATCH:
            matched.append(alert)
        elif fallback is LocationMatch.NO_MATCH:
            nonmatching.append(alert)
        else:
            unresolved.append(alert)

    return CapLocationResult(
        matched=tuple(matched),
        nonmatching=tuple(nonmatching),
        unresolved=tuple(unresolved),
    )


def load_cap_archive(
    archive: Path,
    *,
    now: datetime,
    longitude: float,
    latitude: float,
    resolver: WarningAreaResolver | None = None,
) -> CapArchiveResult:
    """Parse every XML member; any malformed member invalidates the snapshot."""

    try:
        infos = validate_zip(archive)
    except UnsafeZipError as exc:
        raise CapArchiveError(f"CAP archive validation failed: {exc}") from exc

    alerts: list[CapAlert] = []
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            for info in infos:
                try:
                    alerts.append(parse_cap_xml(bundle.read(info)))
                except (OSError, RuntimeError) as exc:
                    raise CapArchiveError(
                        f"CAP member could not be parsed safely: {info.filename}: {exc}"
                    ) from exc
    except zipfile.BadZipFile as exc:
        raise CapArchiveError("CAP archive became unreadable during parsing") from exc

    if len(alerts) != len(infos):
        raise CapArchiveError("CAP archive parsing was incomplete")
    snapshot = resolve_cap_snapshot(alerts, now=now)
    location = _resolve_location(
        snapshot,
        longitude=longitude,
        latitude=latitude,
        resolver=resolver,
    )
    return CapArchiveResult(
        source_file=archive.name,
        source_sha256=_sha256(archive),
        archive_entries=len(infos),
        parsed_alerts=len(alerts),
        snapshot=snapshot,
        location=location,
    )


_HARDWARE_EVENT_TERMS = (
    "GEWITTER",
    "STARKREGEN",
    "DAUERREGEN",
    "REGEN",
    "WIND",
    "STURM",
    "ORKAN",
    "HAGEL",
)


def _hardware_relevant(info: CapInfo) -> bool:
    event = info.event.upper()
    return any(term in event for term in _HARDWARE_EVENT_TERMS)


def _risk_for_warning(info: CapInfo) -> RiskState:
    if info.severity in {"Severe", "Extreme"}:
        return RiskState.RED
    return RiskState.YELLOW


def cap_hazard_evidence(result: CapArchiveResult) -> tuple[Evidence, ...]:
    """Map only location-matched weather hazards into local safety evidence."""

    evidence: list[Evidence] = []
    for alert in result.location.matched:
        info = alert.german_info()
        if info is None or not _hardware_relevant(info):
            continue
        risk = _risk_for_warning(info)
        headline = info.headline or info.event
        evidence.append(
            Evidence(
                source="DWD_CAP",
                state=risk,
                reason_code=(
                    "CAP_RELEVANT_WARNING_RED"
                    if risk is RiskState.RED
                    else "CAP_RELEVANT_WARNING_YELLOW"
                ),
                reason=f"Aktive amtliche DWD-Warnung am Standort: {headline}",
            )
        )
    return tuple(evidence)
