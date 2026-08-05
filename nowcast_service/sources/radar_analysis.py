"""Conservative site-centred analysis of a validated DWD RV nowcast cycle."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import cast

import numpy as np
from numpy.typing import NDArray

from nowcast_service.decision_engine import Evidence, RiskState
from nowcast_service.sources.radar_hdf5 import GridIndex, RadarFrame, RadarFrameMetadata

type BoolArray = NDArray[np.bool_]
type FloatArray = NDArray[np.float64]


class RadarAnalysisError(RuntimeError):
    """Raised when a radar cycle cannot be evaluated consistently."""


class ArrivalConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class RadarAnalysisConfig:
    site_radius_km: float = 2.0
    search_radius_km: float = 100.0
    weak_threshold_mm_5min: float = 0.001
    single_pixel_threshold_mm_5min: float = 0.01
    remote_threshold_mm_5min: float = 0.001
    min_weak_site_pixels: int = 2
    min_component_pixels: int = 3
    min_valid_fraction: float = 0.80
    min_approach_km: float = 0.5
    min_approach_steps: int = 2
    ring_radii_km: tuple[float, ...] = (5.0, 10.0, 25.0, 50.0)
    required_leads: tuple[int, ...] = tuple(range(0, 121, 5))

    def __post_init__(self) -> None:
        positive_values = (
            self.site_radius_km,
            self.search_radius_km,
            self.weak_threshold_mm_5min,
            self.single_pixel_threshold_mm_5min,
            self.remote_threshold_mm_5min,
            self.min_approach_km,
        )
        if any(value <= 0 or not math.isfinite(value) for value in positive_values):
            raise ValueError("Radar analysis distances and thresholds must be positive")
        if self.search_radius_km < max(self.ring_radii_km, default=0.0):
            raise ValueError("Search radius must contain every configured ring")
        if not 0 < self.min_valid_fraction <= 1:
            raise ValueError("min_valid_fraction must be in the interval (0, 1]")
        if self.min_weak_site_pixels < 1 or self.min_component_pixels < 1:
            raise ValueError("Radar pixel-count thresholds must be positive")
        if self.min_approach_steps < 1:
            raise ValueError("min_approach_steps must be positive")
        if not self.required_leads or self.required_leads[0] != 0:
            raise ValueError("Required radar leads must start at zero")
        if tuple(sorted(set(self.required_leads))) != self.required_leads:
            raise ValueError("Required radar leads must be unique and sorted")
        if any(lead < 0 or lead % 5 for lead in self.required_leads):
            raise ValueError("Required radar leads must use the five-minute grid")


DEFAULT_RADAR_ANALYSIS_CONFIG = RadarAnalysisConfig()


@dataclass(frozen=True, slots=True)
class RingStatistics:
    radius_km: float
    valid_fraction: float
    wet_pixel_count: int
    maximum_mm_5min: float | None
    wet_p90_mm_5min: float | None


@dataclass(frozen=True, slots=True)
class RainComponent:
    pixel_count: int
    area_km2: float
    nearest_distance_km: float
    nearest_bearing_deg: float
    maximum_mm_5min: float


@dataclass(frozen=True, slots=True)
class RadarFrameAnalysis:
    lead_minutes: int
    valid_fraction_site: float
    coverage_sufficient: bool
    rain_at_site: bool
    site_wet_pixel_count: int
    site_maximum_mm_5min: float | None
    nearest_component: RainComponent | None
    rings: tuple[RingStatistics, ...]


@dataclass(frozen=True, slots=True)
class ArrivalWindow:
    earliest_minutes: int
    estimate_minutes: int
    latest_minutes: int
    confidence: ArrivalConfidence


@dataclass(frozen=True, slots=True)
class RadarCycleAnalysis:
    available_leads: tuple[int, ...]
    missing_leads: tuple[int, ...]
    coverage_complete_to_60: bool
    coverage_complete_to_120: bool
    rain_now: bool
    moving_toward_site: bool
    arrival: ArrivalWindow | None
    maximum_site_amount_mm_5min: float | None
    maximum_site_amount_lead_minutes: int | None
    frames: tuple[RadarFrameAnalysis, ...]


@dataclass(frozen=True, slots=True)
class _ComponentPixel:
    row: int
    column: int
    value: float


def _decode_arrays(frame: RadarFrame) -> tuple[BoolArray, FloatArray]:
    raw = np.asarray(frame.data)
    metadata = frame.metadata
    coverage = np.asarray(raw != metadata.nodata, dtype=np.bool_)
    values = np.asarray(
        raw.astype(np.float64) * metadata.gain + metadata.offset,
        dtype=np.float64,
    )
    values[raw == metadata.undetect] = 0.0
    values[raw == metadata.nodata] = np.nan
    return coverage, values


def _crop_geometry(
    metadata: RadarFrameMetadata,
    center: GridIndex,
    radius_km: float,
) -> tuple[slice, slice, FloatArray]:
    radius_m = radius_km * 1000
    row_extent = math.ceil(radius_m / metadata.yscale_m)
    column_extent = math.ceil(radius_m / metadata.xscale_m)
    row_start = max(0, center.row - row_extent)
    row_stop = min(metadata.shape[0], center.row + row_extent + 1)
    column_start = max(0, center.column - column_extent)
    column_stop = min(metadata.shape[1], center.column + column_extent + 1)

    rows = np.arange(row_start, row_stop, dtype=np.float64)
    columns = np.arange(column_start, column_stop, dtype=np.float64)
    row_offsets = (rows - center.row) * metadata.yscale_m
    column_offsets = (columns - center.column) * metadata.xscale_m
    distances = np.hypot(row_offsets[:, None], column_offsets[None, :]) / 1000
    return slice(row_start, row_stop), slice(column_start, column_stop), distances


def _ring_statistics(
    *,
    radius_km: float,
    distances_km: FloatArray,
    coverage: BoolArray,
    values: FloatArray,
    weak_threshold: float,
) -> RingStatistics:
    ring = distances_km <= radius_km
    total = int(np.count_nonzero(ring))
    valid = ring & coverage
    valid_count = int(np.count_nonzero(valid))
    valid_fraction = valid_count / total if total else 0.0
    valid_values = values[valid]
    wet_values = valid_values[valid_values >= weak_threshold]
    maximum = float(np.max(valid_values)) if valid_values.size else None
    wet_p90 = float(np.percentile(wet_values, 90)) if wet_values.size else None
    return RingStatistics(
        radius_km=radius_km,
        valid_fraction=valid_fraction,
        wet_pixel_count=int(wet_values.size),
        maximum_mm_5min=maximum,
        wet_p90_mm_5min=wet_p90,
    )


def _component_pixels(
    mask: BoolArray,
    values: FloatArray,
) -> tuple[tuple[_ComponentPixel, ...], ...]:
    rows, columns = mask.shape
    visited = np.zeros(mask.shape, dtype=np.bool_)
    components: list[tuple[_ComponentPixel, ...]] = []
    neighbors = (
        (-1, -1),
        (-1, 0),
        (-1, 1),
        (0, -1),
        (0, 1),
        (1, -1),
        (1, 0),
        (1, 1),
    )

    for row in range(rows):
        for column in range(columns):
            if not mask[row, column] or visited[row, column]:
                continue
            queue: deque[tuple[int, int]] = deque([(row, column)])
            visited[row, column] = True
            pixels: list[_ComponentPixel] = []
            while queue:
                current_row, current_column = queue.popleft()
                pixels.append(
                    _ComponentPixel(
                        row=current_row,
                        column=current_column,
                        value=float(values[current_row, current_column]),
                    )
                )
                for delta_row, delta_column in neighbors:
                    candidate_row = current_row + delta_row
                    candidate_column = current_column + delta_column
                    if not (0 <= candidate_row < rows and 0 <= candidate_column < columns):
                        continue
                    if visited[candidate_row, candidate_column]:
                        continue
                    if not mask[candidate_row, candidate_column]:
                        continue
                    visited[candidate_row, candidate_column] = True
                    queue.append((candidate_row, candidate_column))
            components.append(tuple(pixels))
    return tuple(components)


def _rain_components(
    *,
    active: BoolArray,
    values: FloatArray,
    row_start: int,
    column_start: int,
    center: GridIndex,
    metadata: RadarFrameMetadata,
    min_pixels: int,
) -> tuple[RainComponent, ...]:
    results: list[RainComponent] = []
    for component in _component_pixels(active, values):
        if len(component) < min_pixels:
            continue
        nearest_distance = math.inf
        nearest_bearing = 0.0
        maximum = 0.0
        for pixel in component:
            global_row = row_start + pixel.row
            global_column = column_start + pixel.column
            east_m = (global_column - center.column) * metadata.xscale_m
            north_m = (center.row - global_row) * metadata.yscale_m
            distance_km = math.hypot(east_m, north_m) / 1000
            if distance_km < nearest_distance:
                nearest_distance = distance_km
                nearest_bearing = math.degrees(math.atan2(east_m, north_m)) % 360
            maximum = max(maximum, pixel.value)
        area_km2 = len(component) * metadata.xscale_m * metadata.yscale_m / 1_000_000
        results.append(
            RainComponent(
                pixel_count=len(component),
                area_km2=area_km2,
                nearest_distance_km=nearest_distance,
                nearest_bearing_deg=nearest_bearing,
                maximum_mm_5min=maximum,
            )
        )
    return tuple(sorted(results, key=lambda item: item.nearest_distance_km))


def analyze_radar_frame(
    frame: RadarFrame,
    *,
    longitude: float,
    latitude: float,
    config: RadarAnalysisConfig = DEFAULT_RADAR_ANALYSIS_CONFIG,
) -> RadarFrameAnalysis:
    if frame.metadata.product != "DWD_RV":
        raise RadarAnalysisError("Only DWD RV frames can be analysed quantitatively")
    center = frame.metadata.index_for_lonlat(longitude, latitude)
    coverage_full, values_full = _decode_arrays(frame)
    row_slice, column_slice, distances = _crop_geometry(
        frame.metadata,
        center,
        config.search_radius_km,
    )
    coverage = coverage_full[row_slice, column_slice]
    values = values_full[row_slice, column_slice]
    search_mask = distances <= config.search_radius_km

    site_mask = distances <= config.site_radius_km
    site_total = int(np.count_nonzero(site_mask))
    site_valid = site_mask & coverage
    site_valid_count = int(np.count_nonzero(site_valid))
    valid_fraction = site_valid_count / site_total if site_total else 0.0
    site_values = values[site_valid]
    site_maximum = float(np.max(site_values)) if site_values.size else None
    weak_pixels = int(
        np.count_nonzero(site_valid & (values >= config.weak_threshold_mm_5min))
    )
    rain_at_site = bool(
        site_maximum is not None
        and (
            site_maximum >= config.single_pixel_threshold_mm_5min
            or weak_pixels >= config.min_weak_site_pixels
        )
    )

    active = (
        search_mask
        & coverage
        & np.asarray(values >= config.remote_threshold_mm_5min, dtype=np.bool_)
    )
    components = _rain_components(
        active=active,
        values=values,
        row_start=cast(int, row_slice.start),
        column_start=cast(int, column_slice.start),
        center=center,
        metadata=frame.metadata,
        min_pixels=config.min_component_pixels,
    )
    rings = tuple(
        _ring_statistics(
            radius_km=radius,
            distances_km=distances,
            coverage=coverage,
            values=values,
            weak_threshold=config.weak_threshold_mm_5min,
        )
        for radius in config.ring_radii_km
    )
    return RadarFrameAnalysis(
        lead_minutes=frame.metadata.lead_minutes,
        valid_fraction_site=valid_fraction,
        coverage_sufficient=valid_fraction >= config.min_valid_fraction,
        rain_at_site=rain_at_site,
        site_wet_pixel_count=weak_pixels,
        site_maximum_mm_5min=site_maximum,
        nearest_component=components[0] if components else None,
        rings=rings,
    )


def _same_grid(first: RadarFrameMetadata, other: RadarFrameMetadata) -> bool:
    exact = (
        first.product == other.product
        and first.reference_time == other.reference_time
        and first.shape == other.shape
        and first.projection == other.projection
        and first.nodata == other.nodata
        and first.undetect == other.undetect
    )
    floats = (
        (first.gain, other.gain),
        (first.offset, other.offset),
        (first.xscale_m, other.xscale_m),
        (first.yscale_m, other.yscale_m),
        (first.left_edge_m, other.left_edge_m),
        (first.right_edge_m, other.right_edge_m),
        (first.top_edge_m, other.top_edge_m),
        (first.bottom_edge_m, other.bottom_edge_m),
    )
    return exact and all(math.isclose(left, right) for left, right in floats)


def _coverage_complete(
    analyses: dict[int, RadarFrameAnalysis],
    *,
    maximum_lead: int,
) -> bool:
    required = range(0, maximum_lead + 1, 5)
    return all(
        lead in analyses and analyses[lead].coverage_sufficient
        for lead in required
    )


def _arrival(frames: tuple[RadarFrameAnalysis, ...]) -> ArrivalWindow | None:
    wet = tuple(frame for frame in frames if frame.rain_at_site)
    if not wet:
        return None
    first = wet[0]
    if first.lead_minutes == 0:
        return ArrivalWindow(0, 0, 0, ArrivalConfidence.HIGH)

    by_lead = {frame.lead_minutes: frame for frame in frames}
    previous = by_lead.get(first.lead_minutes - 5)
    following = by_lead.get(first.lead_minutes + 5)
    previous_dry = previous is not None and not previous.rain_at_site
    following_wet = following is not None and following.rain_at_site
    if previous_dry and following_wet:
        confidence = ArrivalConfidence.HIGH
    elif previous_dry or following_wet:
        confidence = ArrivalConfidence.MEDIUM
    else:
        confidence = ArrivalConfidence.LOW
    earliest = max(0, first.lead_minutes - 5)
    return ArrivalWindow(
        earliest_minutes=earliest,
        estimate_minutes=first.lead_minutes,
        latest_minutes=first.lead_minutes,
        confidence=confidence,
    )


def _moving_toward(
    frames: tuple[RadarFrameAnalysis, ...],
    arrival: ArrivalWindow | None,
    config: RadarAnalysisConfig,
) -> bool:
    if arrival is not None and arrival.estimate_minutes > 0:
        prior_component = any(
            frame.lead_minutes < arrival.estimate_minutes
            and frame.nearest_component is not None
            for frame in frames
        )
        if prior_component:
            return True

    distances = tuple(
        (frame.lead_minutes, frame.nearest_component.nearest_distance_km)
        for frame in frames
        if frame.nearest_component is not None
    )
    approach_steps = 0
    for previous, current in pairwise(distances):
        consecutive = current[0] - previous[0] == 5
        approaching = previous[1] - current[1] >= config.min_approach_km
        if consecutive and approaching:
            approach_steps += 1
            if approach_steps >= config.min_approach_steps:
                return True
        else:
            approach_steps = 0
    return False


def analyze_radar_cycle(
    frames: Iterable[RadarFrame],
    *,
    longitude: float,
    latitude: float,
    config: RadarAnalysisConfig = DEFAULT_RADAR_ANALYSIS_CONFIG,
) -> RadarCycleAnalysis:
    items = tuple(frames)
    if not items:
        raise RadarAnalysisError("Radar cycle is empty")
    first = items[0].metadata
    if first.product != "DWD_RV":
        raise RadarAnalysisError("Radar cycle must contain DWD RV frames")
    leads = [frame.metadata.lead_minutes for frame in items]
    if len(set(leads)) != len(leads):
        raise RadarAnalysisError("Radar cycle contains duplicate lead times")
    if any(not _same_grid(first, frame.metadata) for frame in items[1:]):
        raise RadarAnalysisError("Radar cycle contains inconsistent grids or reference times")

    analyses = tuple(
        sorted(
            (
                analyze_radar_frame(
                    frame,
                    longitude=longitude,
                    latitude=latitude,
                    config=config,
                )
                for frame in items
            ),
            key=lambda item: item.lead_minutes,
        )
    )
    by_lead = {frame.lead_minutes: frame for frame in analyses}
    available = tuple(sorted(by_lead))
    missing = tuple(lead for lead in config.required_leads if lead not in by_lead)
    arrival = _arrival(analyses)
    maxima = tuple(
        (frame.site_maximum_mm_5min, frame.lead_minutes)
        for frame in analyses
        if frame.site_maximum_mm_5min is not None
    )
    if maxima:
        maximum, maximum_lead = max(maxima, key=lambda item: cast(float, item[0]))
    else:
        maximum, maximum_lead = None, None
    zero = by_lead.get(0)
    return RadarCycleAnalysis(
        available_leads=available,
        missing_leads=missing,
        coverage_complete_to_60=_coverage_complete(by_lead, maximum_lead=60),
        coverage_complete_to_120=_coverage_complete(by_lead, maximum_lead=120),
        rain_now=zero.rain_at_site if zero is not None else False,
        moving_toward_site=_moving_toward(analyses, arrival, config),
        arrival=arrival,
        maximum_site_amount_mm_5min=maximum,
        maximum_site_amount_lead_minutes=maximum_lead,
        frames=analyses,
    )


def radar_hazard_evidence(
    analysis: RadarCycleAnalysis,
    *,
    red_arrival_minutes: int = 60,
    yellow_arrival_minutes: int = 120,
) -> tuple[Evidence, ...]:
    if red_arrival_minutes <= 0 or yellow_arrival_minutes <= red_arrival_minutes:
        raise ValueError("Radar hazard arrival thresholds are invalid")
    if analysis.rain_now:
        return (
            Evidence(
                source="DWD_RV",
                state=RiskState.RED,
                reason_code="RADAR_RAIN_AT_SITE",
                reason="Das DWD-Radar erkennt Niederschlag am Standort.",
            ),
        )
    arrival = analysis.arrival
    if arrival is not None and arrival.estimate_minutes <= red_arrival_minutes:
        return (
            Evidence(
                source="DWD_RV",
                state=RiskState.RED,
                reason_code="RADAR_ARRIVAL_WITHIN_60_MIN",
                reason=(
                    "Niederschlag erreicht den Standort voraussichtlich in etwa "
                    f"{arrival.estimate_minutes} Minuten."
                ),
            ),
        )
    if arrival is not None and arrival.estimate_minutes <= yellow_arrival_minutes:
        return (
            Evidence(
                source="DWD_RV",
                state=RiskState.YELLOW,
                reason_code="RADAR_ARRIVAL_61_TO_120_MIN",
                reason=(
                    "Niederschlag könnte den Standort in etwa "
                    f"{arrival.estimate_minutes} Minuten erreichen."
                ),
            ),
        )
    if analysis.moving_toward_site:
        return (
            Evidence(
                source="DWD_RV",
                state=RiskState.YELLOW,
                reason_code="RADAR_AREA_APPROACHING",
                reason=(
                    "Ein belastbares Niederschlagsgebiet nähert sich, aber die "
                    "Ankunftszeit ist noch nicht sicher."
                ),
            ),
        )
    return ()
