"""Production DWD RV source cycle built on the existing strict parser."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from nowcast_service.config import AppConfig
from nowcast_service.decision_engine import SourceState
from nowcast_service.runtime.models import SourceSnapshot, iso
from nowcast_service.sources.runtime_base import SourceRunError


def _bearing_name(value: float) -> str:
    names = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return names[int((value + 22.5) // 45) % 8]


class RadarSourceRunner:
    source_id = "DWD_RV"

    def __init__(self, *, config: AppConfig, data_dir: Path) -> None:
        self._config = config
        self._data_dir = Path(data_dir)

    async def run(self, *, evaluated_at: Any) -> SourceSnapshot:
        from nowcast_service.downloads.remote import DownloadLimits, download_atomic
        from nowcast_service.downloads.safe_tar import extract_tar_safely
        from nowcast_service.sources.dwd_directory import DwdDirectoryClient, RV_SPEC
        from nowcast_service.sources.radar_analysis import analyze_radar_cycle, radar_hazard_evidence
        from nowcast_service.sources.radar_hdf5 import load_radar_frame

        errors: list[str] = []
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            try:
                candidates = await DwdDirectoryClient(client).candidates(RV_SPEC)
            except Exception as exc:
                raise SourceRunError("RADAR_DIRECTORY_FAILED", type(exc).__name__) from exc
            for candidate in candidates[:5]:
                try:
                    downloaded = await download_atomic(client, url=candidate.url,
                        destination_dir=self._data_dir / "cache" / "radar",
                        limits=DownloadLimits(max_bytes=64 * 1024 * 1024, allowed_suffixes=(".tar",)))
                    if downloaded is None:
                        raise SourceRunError("RADAR_NOT_MODIFIED_WITHOUT_CACHE", "No cached archive")
                    temp_root = self._data_dir / "temp"
                    temp_root.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(dir=temp_root) as temporary:
                        members = await asyncio.to_thread(extract_tar_safely, downloaded.path, Path(temporary))
                        frames = await asyncio.gather(*(asyncio.to_thread(load_radar_frame, member,
                            expected_product="DWD_RV") for member in members))
                        analysis = await asyncio.to_thread(analyze_radar_cycle, frames,
                            longitude=self._config.location.longitude,
                            latitude=self._config.location.latitude)
                    if analysis.missing_leads or not analysis.coverage_complete_to_120:
                        raise SourceRunError("RADAR_CYCLE_INCOMPLETE",
                            "RV cycle does not provide complete valid coverage to 120 minutes")
                    reference = frames[0].metadata.reference_time
                    age_seconds = max(0, int((evaluated_at - reference).total_seconds()))
                    stale = self._config.thresholds.radar_stale_after_minutes * 60
                    invalid = self._config.thresholds.radar_invalid_after_minutes * 60
                    state = SourceState.LIVE
                    failure_code = failure_message = None
                    if age_seconds > invalid:
                        state, failure_code = SourceState.FAILED, "RADAR_SOURCE_TOO_OLD"
                        failure_message = "Latest complete RV cycle is older than the invalid limit"
                    elif age_seconds > stale:
                        state, failure_code = SourceState.STALE, "RADAR_SOURCE_STALE"
                        failure_message = "Latest complete RV cycle is stale"
                    current = next((frame for frame in analysis.frames if frame.lead_minutes == 0), None)
                    nearest = current.nearest_component if current else None
                    arrival = analysis.arrival
                    hold_minutes = self._config.thresholds.radar_latch_margin_minutes
                    if arrival is not None:
                        hold_minutes += arrival.latest_minutes
                    elif analysis.rain_now:
                        hold_minutes += 30
                    payload: dict[str, Any] = {
                        "cycleTime": iso(reference), "frameCount": len(frames),
                        "forecastHorizonMinutes": max(analysis.available_leads),
                        "availableLeadMinutes": analysis.available_leads,
                        "missingLeadMinutes": analysis.missing_leads,
                        "coverage0To60": analysis.coverage_complete_to_60,
                        "coverage0To120": analysis.coverage_complete_to_120,
                        "rainNow": analysis.rain_now, "movingTowardSite": analysis.moving_toward_site,
                        "arrivalMinutes": arrival.estimate_minutes if arrival else None,
                        "arrivalWindow": ({"earliest": arrival.earliest_minutes,
                            "latest": arrival.latest_minutes, "confidence": arrival.confidence}
                            if arrival else None),
                        "nearestPrecipitationDistanceKm": nearest.nearest_distance_km if nearest else None,
                        "nearestPrecipitationDirection": _bearing_name(nearest.nearest_bearing_deg) if nearest else None,
                        "affectedAreaKm2": nearest.area_km2 if nearest else None,
                        "peakIntensityMm5Min": analysis.maximum_site_amount_mm_5min,
                        "peakIntensityLeadMinutes": analysis.maximum_site_amount_lead_minutes,
                        "siteIntensityMm5Min": current.site_maximum_mm_5min if current else None,
                        "unit": "mm/5min",
                        "hazardHoldUntil": iso(evaluated_at + timedelta(minutes=hold_minutes))}
                    evidence = radar_hazard_evidence(analysis,
                        red_arrival_minutes=self._config.thresholds.radar_red_arrival_minutes,
                        yellow_arrival_minutes=self._config.thresholds.radar_yellow_arrival_minutes)
                    return SourceSnapshot(source_id=self.source_id,
                        source_input_id=f"DWD_RV:{downloaded.sha256}:{reference.isoformat()}",
                        product="DWD_RV", cycle_time=reference, downloaded_at=evaluated_at,
                        parsed_at=evaluated_at, valid_from=reference,
                        valid_until=reference + timedelta(seconds=invalid), evaluated_at=evaluated_at,
                        state=state, content_sha256=downloaded.sha256, is_complete=True,
                        stale_after_seconds=stale, invalid_after_seconds=invalid,
                        failure_code=failure_code, failure_message=failure_message,
                        payload=payload, evidence=evidence)
                except Exception as exc:
                    errors.append(f"{candidate.name}:{type(exc).__name__}")
        raise SourceRunError("RADAR_CYCLE_FAILED",
            "No complete RV candidate could be processed (" + ", ".join(errors[:5]) + ")")
