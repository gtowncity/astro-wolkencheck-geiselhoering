from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from nowcast_service.decision_engine import RiskState
from nowcast_service.sources.radar_analysis import (
    ArrivalConfidence,
    RadarAnalysisConfig,
    RadarAnalysisError,
    analyze_radar_cycle,
    analyze_radar_frame,
    radar_hazard_evidence,
)
from nowcast_service.sources.radar_hdf5 import RadarFrame, RadarFrameMetadata

REFERENCE = datetime(2026, 8, 5, 6, 25, tzinfo=UTC)
CENTER = (50, 50)


def metadata(
    lead: int,
    *,
    product: str = "DWD_RV",
    reference: datetime = REFERENCE,
) -> RadarFrameMetadata:
    valid_end = reference + timedelta(minutes=lead)
    return RadarFrameMetadata(
        product=product,
        member_name=f"composite_rv_20260805_0625_{lead:03d}-hd5",
        conventions="ODIM_H5/V2_3",
        reference_time=reference,
        lead_minutes=lead,
        interval_start=valid_end - timedelta(minutes=5),
        interval_end=valid_end,
        simulated=lead > 0,
        quantity="ACRR" if product == "DWD_RV" else "DBZH",
        unit="mm/5min" if product == "DWD_RV" else "dBZ",
        gain=0.001,
        offset=0.0,
        nodata=65535,
        undetect=0,
        shape=(101, 101),
        dtype="uint16",
        projection="EPSG:3857",
        xscale_m=1000.0,
        yscale_m=1000.0,
        left_edge_m=-50_500.0,
        right_edge_m=50_500.0,
        top_edge_m=50_500.0,
        bottom_edge_m=-50_500.0,
    )


def frame(
    lead: int,
    *,
    points: tuple[tuple[int, int, int], ...] = (),
    nodata_site: bool = False,
    product: str = "DWD_RV",
    reference: datetime = REFERENCE,
) -> RadarFrame:
    data = np.zeros((101, 101), dtype=np.uint16)
    if nodata_site:
        data[48:53, 48:53] = 65535
    for row, column, raw in points:
        data[row, column] = raw
    return RadarFrame(
        metadata=metadata(lead, product=product, reference=reference),
        data=data,
    )


def site_points(raw: int = 2) -> tuple[tuple[int, int, int], ...]:
    return (
        (CENTER[0], CENTER[1], raw),
        (CENTER[0], CENTER[1] + 1, raw),
    )


def cluster(column: int, raw: int = 2) -> tuple[tuple[int, int, int], ...]:
    return (
        (CENTER[0] - 1, column, raw),
        (CENTER[0], column, raw),
        (CENTER[0] + 1, column, raw),
    )


def full_cycle(
    *,
    site_leads: tuple[int, ...] = (),
    remote_by_lead: dict[int, int] | None = None,
) -> tuple[RadarFrame, ...]:
    remote_by_lead = remote_by_lead or {}
    frames: list[RadarFrame] = []
    for lead in range(0, 121, 5):
        points: tuple[tuple[int, int, int], ...] = ()
        if lead in site_leads:
            points += site_points(11)
        if lead in remote_by_lead:
            points += cluster(remote_by_lead[lead])
        frames.append(frame(lead, points=points))
    return tuple(frames)


def test_default_config_rejects_invalid_safety_values() -> None:
    with pytest.raises(ValueError, match="positive"):
        RadarAnalysisConfig(site_radius_km=0)
    with pytest.raises(ValueError, match="contain"):
        RadarAnalysisConfig(search_radius_km=10)
    with pytest.raises(ValueError, match="valid_fraction"):
        RadarAnalysisConfig(min_valid_fraction=0)
    with pytest.raises(ValueError, match="pixel-count"):
        RadarAnalysisConfig(min_component_pixels=0)
    with pytest.raises(ValueError, match="start at zero"):
        RadarAnalysisConfig(required_leads=(5, 10))
    with pytest.raises(ValueError, match="unique and sorted"):
        RadarAnalysisConfig(required_leads=(0, 10, 5))
    with pytest.raises(ValueError, match="five-minute"):
        RadarAnalysisConfig(required_leads=(0, 7))


def test_dry_complete_cycle_has_full_coverage_without_hazard() -> None:
    result = analyze_radar_cycle(full_cycle(), longitude=0, latitude=0)

    assert result.available_leads == tuple(range(0, 121, 5))
    assert result.missing_leads == ()
    assert result.coverage_complete_to_60 is True
    assert result.coverage_complete_to_120 is True
    assert result.rain_now is False
    assert result.arrival is None
    assert result.moving_toward_site is False
    assert radar_hazard_evidence(result) == ()


def test_two_weak_site_pixels_trigger_immediate_red() -> None:
    result = analyze_radar_cycle(
        full_cycle(site_leads=(0,)),
        longitude=0,
        latitude=0,
    )
    evidence = radar_hazard_evidence(result)

    assert result.rain_now is True
    assert result.arrival is not None
    assert result.arrival.confidence is ArrivalConfidence.HIGH
    assert evidence[0].state is RiskState.RED
    assert evidence[0].reason_code == "RADAR_RAIN_AT_SITE"


def test_single_strong_site_pixel_triggers_but_single_weak_pixel_does_not() -> None:
    strong = analyze_radar_frame(
        frame(0, points=((50, 50, 11),)),
        longitude=0,
        latitude=0,
    )
    weak = analyze_radar_frame(
        frame(0, points=((50, 50, 2),)),
        longitude=0,
        latitude=0,
    )

    assert strong.rain_at_site is True
    assert weak.rain_at_site is False


def test_isolated_remote_pixel_is_filtered_but_small_area_is_retained() -> None:
    analysed = analyze_radar_frame(
        frame(
            0,
            points=((50, 70, 2), *cluster(80)),
        ),
        longitude=0,
        latitude=0,
    )

    component = analysed.nearest_component
    assert component is not None
    assert component.pixel_count == 3
    assert component.area_km2 == pytest.approx(3.0)
    assert component.nearest_distance_km == pytest.approx(30.0)
    assert component.nearest_bearing_deg == pytest.approx(90.0)
    assert analysed.rings[-1].wet_pixel_count == 4


def test_arrival_in_45_minutes_is_red_and_moving_toward_site() -> None:
    cycle = full_cycle(
        site_leads=(45, 50),
        remote_by_lead={0: 80, 5: 75, 10: 70},
    )

    result = analyze_radar_cycle(cycle, longitude=0, latitude=0)
    evidence = radar_hazard_evidence(result)

    assert result.arrival is not None
    assert result.arrival.earliest_minutes == 40
    assert result.arrival.estimate_minutes == 45
    assert result.arrival.latest_minutes == 45
    assert result.arrival.confidence is ArrivalConfidence.HIGH
    assert result.moving_toward_site is True
    assert evidence[0].state is RiskState.RED
    assert evidence[0].reason_code == "RADAR_ARRIVAL_WITHIN_60_MIN"


def test_arrival_in_90_minutes_is_yellow() -> None:
    result = analyze_radar_cycle(
        full_cycle(site_leads=(90, 95)),
        longitude=0,
        latitude=0,
    )
    evidence = radar_hazard_evidence(result)

    assert result.arrival is not None
    assert result.arrival.estimate_minutes == 90
    assert evidence[0].state is RiskState.YELLOW
    assert evidence[0].reason_code == "RADAR_ARRIVAL_61_TO_120_MIN"


def test_approaching_components_without_site_arrival_are_yellow() -> None:
    frames = (
        frame(0, points=cluster(80)),
        frame(5, points=cluster(75)),
        frame(10, points=cluster(70)),
    )

    result = analyze_radar_cycle(frames, longitude=0, latitude=0)
    evidence = radar_hazard_evidence(result)

    assert result.arrival is None
    assert result.moving_toward_site is True
    assert evidence[0].state is RiskState.YELLOW
    assert evidence[0].reason_code == "RADAR_AREA_APPROACHING"


def test_missing_frame_and_site_nodata_prevent_complete_coverage() -> None:
    missing = tuple(frame(lead) for lead in range(0, 121, 5) if lead != 60)
    missing_result = analyze_radar_cycle(missing, longitude=0, latitude=0)
    nodata_cycle = tuple(frame(lead, nodata_site=lead == 30) for lead in range(0, 121, 5))
    nodata_result = analyze_radar_cycle(nodata_cycle, longitude=0, latitude=0)

    assert missing_result.missing_leads == (60,)
    assert missing_result.coverage_complete_to_60 is False
    assert missing_result.coverage_complete_to_120 is False
    assert nodata_result.frames[6].coverage_sufficient is False
    assert nodata_result.coverage_complete_to_60 is False


def test_cycle_rejects_empty_duplicate_inconsistent_and_non_rv_inputs() -> None:
    with pytest.raises(RadarAnalysisError, match="empty"):
        analyze_radar_cycle((), longitude=0, latitude=0)
    with pytest.raises(RadarAnalysisError, match="duplicate"):
        analyze_radar_cycle((frame(0), frame(0)), longitude=0, latitude=0)
    with pytest.raises(RadarAnalysisError, match="inconsistent"):
        analyze_radar_cycle(
            (
                frame(0),
                frame(5, reference=REFERENCE + timedelta(minutes=5)),
            ),
            longitude=0,
            latitude=0,
        )
    with pytest.raises(RadarAnalysisError, match="DWD RV"):
        analyze_radar_cycle((frame(0, product="DWD_WN"),), longitude=0, latitude=0)
    with pytest.raises(RadarAnalysisError, match="quantitatively"):
        analyze_radar_frame(
            frame(0, product="DWD_WN"),
            longitude=0,
            latitude=0,
        )


def test_invalid_evidence_thresholds_are_rejected() -> None:
    result = analyze_radar_cycle(full_cycle(), longitude=0, latitude=0)

    with pytest.raises(ValueError, match="invalid"):
        radar_hazard_evidence(
            result,
            red_arrival_minutes=60,
            yellow_arrival_minutes=60,
        )


def test_maximum_site_amount_and_lead_are_reported() -> None:
    frames = list(full_cycle(site_leads=(45, 50)))
    frames[10] = frame(50, points=site_points(31))

    result = analyze_radar_cycle(frames, longitude=0, latitude=0)

    assert result.maximum_site_amount_mm_5min == pytest.approx(0.031)
    assert result.maximum_site_amount_lead_minutes == 50


def test_metadata_variation_is_detected() -> None:
    first = frame(0)
    changed = frame(5)
    changed = RadarFrame(
        metadata=replace(changed.metadata, xscale_m=2000.0),
        data=changed.data,
    )

    with pytest.raises(RadarAnalysisError, match="inconsistent"):
        analyze_radar_cycle((first, changed), longitude=0, latitude=0)
