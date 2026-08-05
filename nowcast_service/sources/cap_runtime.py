"""Production DWD CAP source cycle using complete archive semantics."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from nowcast_service.config import AppConfig
from nowcast_service.decision_engine import Evidence, RiskState, SourceState
from nowcast_service.runtime.models import SourceSnapshot, iso
from nowcast_service.sources.runtime_base import SourceRunError

_CRITICAL_EVENT_TERMS = ("GEWITTER", "STARKREGEN", "DAUERREGEN")
_HARDWARE_EVENT_TERMS = (*_CRITICAL_EVENT_TERMS, "REGEN", "WIND", "STURM", "ORKAN", "HAGEL")


def _cap_evidence(alerts: tuple[Any, ...]) -> tuple[Evidence, ...]:
    evidence: list[Evidence] = []
    for alert in alerts:
        info = alert.german_info()
        if info is None:
            continue
        event = info.event.upper()
        if not any(term in event for term in _HARDWARE_EVENT_TERMS):
            continue
        critical = any(term in event for term in _CRITICAL_EVENT_TERMS)
        risk = (
            RiskState.RED
            if critical or info.severity in {"Severe", "Extreme"}
            else RiskState.YELLOW
        )
        evidence.append(
            Evidence(
                source="DWD_CAP",
                state=risk,
                reason_code="CAP_RELEVANT_WARNING_RED"
                if risk is RiskState.RED
                else "CAP_RELEVANT_WARNING_YELLOW",
                reason=f"Active official DWD warning at the site: {info.headline or info.event}",
            )
        )
    return tuple(evidence)


class CapSourceRunner:
    source_id = "DWD_CAP"

    def __init__(self, *, config: AppConfig, data_dir: Path) -> None:
        self._config = config
        self._data_dir = Path(data_dir)
        self._warning_index: Any = None
        self._warning_index_loaded_at: Any = None

    async def run(self, *, evaluated_at: Any) -> SourceSnapshot:
        from nowcast_service.downloads.remote import DownloadLimits, download_atomic
        from nowcast_service.sources.cap_snapshot import load_cap_archive
        from nowcast_service.sources.dwd_cap_directory import (
            CAP_COMMUNE_SPEC,
            DwdCapDirectoryClient,
        )
        from nowcast_service.sources.warning_area_index import DwdWarningAreaClient

        timeout = httpx.Timeout(30.0, connect=10.0)
        errors: list[str] = []
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            if (
                self._warning_index is None
                or self._warning_index_loaded_at is None
                or (evaluated_at - self._warning_index_loaded_at) > timedelta(hours=24)
            ):
                try:
                    self._warning_index = await DwdWarningAreaClient(client).fetch(
                        longitude=self._config.location.longitude,
                        latitude=self._config.location.latitude,
                        retrieved_at=evaluated_at,
                    )
                    self._warning_index_loaded_at = evaluated_at
                except Exception as exc:
                    raise SourceRunError("CAP_WARNING_AREA_FAILED", type(exc).__name__) from exc
            try:
                candidates = await DwdCapDirectoryClient(client).candidates(CAP_COMMUNE_SPEC)
            except Exception as exc:
                raise SourceRunError("CAP_DIRECTORY_FAILED", type(exc).__name__) from exc
            for candidate in candidates[:5]:
                try:
                    downloaded = await download_atomic(
                        client,
                        url=candidate.url,
                        destination_dir=self._data_dir / "cache" / "cap",
                        limits=DownloadLimits(
                            max_bytes=32 * 1024 * 1024, allowed_suffixes=(".zip",)
                        ),
                    )
                    if downloaded is None:
                        raise SourceRunError("CAP_NOT_MODIFIED_WITHOUT_CACHE", "No cached archive")
                    result = await asyncio.to_thread(
                        load_cap_archive,
                        downloaded.path,
                        now=evaluated_at,
                        longitude=self._config.location.longitude,
                        latitude=self._config.location.latitude,
                        resolver=self._warning_index,
                    )
                    unresolved_list = []
                    for alert in result.location.unresolved:
                        info = alert.german_info()
                        if info is not None and info.is_in_force(evaluated_at):
                            unresolved_list.append(alert)
                    unresolved = tuple(unresolved_list)
                    if unresolved:
                        raise SourceRunError(
                            "CAP_LOCATION_UNRESOLVED",
                            "At least one in-force CAP area could not be resolved",
                        )
                    stale = self._config.thresholds.cap_stale_after_minutes * 60
                    invalid = self._config.thresholds.cap_invalid_after_minutes * 60
                    cycle_time = candidate.reference_time or evaluated_at
                    warnings: list[dict[str, Any]] = []
                    expiries = []
                    active_matched = []
                    for alert in result.location.matched:
                        info = alert.german_info()
                        if info is None or not info.is_in_force(evaluated_at):
                            continue
                        active_matched.append(alert)
                        expiries.append(info.expires)
                        warnings.append(
                            {
                                "identifier": alert.identifier,
                                "event": info.event,
                                "severity": info.severity,
                                "urgency": info.urgency,
                                "certainty": info.certainty,
                                "effective": iso(info.effective),
                                "onset": iso(info.onset),
                                "expires": iso(info.expires),
                                "headline": info.headline,
                                "description": info.description,
                                "instruction": info.instruction,
                                "source": "Deutscher Wetterdienst",
                            }
                        )
                    payload: dict[str, Any] = {
                        "active": warnings,
                        "activeCount": len(warnings),
                        "archiveEntries": result.archive_entries,
                        "parsedAlerts": result.parsed_alerts,
                        "unresolvedCount": len(result.location.unresolved),
                        "hazardHoldUntil": iso(max(expiries)) if expiries else None,
                        "source": "Deutscher Wetterdienst",
                    }
                    return SourceSnapshot(
                        source_id=self.source_id,
                        source_input_id=f"DWD_CAP:{downloaded.sha256}:{cycle_time.isoformat()}",
                        product="DWD_CAP_COMMUNE",
                        cycle_time=cycle_time,
                        downloaded_at=evaluated_at,
                        parsed_at=evaluated_at,
                        valid_from=evaluated_at,
                        valid_until=evaluated_at + timedelta(seconds=invalid),
                        evaluated_at=evaluated_at,
                        state=SourceState.LIVE,
                        content_sha256=downloaded.sha256,
                        is_complete=True,
                        stale_after_seconds=stale,
                        invalid_after_seconds=invalid,
                        payload=payload,
                        evidence=_cap_evidence(tuple(active_matched)),
                    )
                except Exception as exc:
                    errors.append(f"{candidate.name}:{type(exc).__name__}")
        raise SourceRunError(
            "CAP_CYCLE_FAILED",
            "No complete CAP candidate could be processed (" + ", ".join(errors[:5]) + ")",
        )
