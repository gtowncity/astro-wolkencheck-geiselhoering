from types import SimpleNamespace

from nowcast_service.sources.radar_analysis import (
    RadarFrameAnalysis,
    RainComponent,
    RingStatistics,
)
from nowcast_service.sources.radar_runtime import _bearing_name, _frame_payload, _ring_payload


def test_bearing_name_uses_eight_cardinal_sectors() -> None:
    assert _bearing_name(0.0) == "N"
    assert _bearing_name(44.9) == "NE"
    assert _bearing_name(90.0) == "E"
    assert _bearing_name(181.0) == "S"
    assert _bearing_name(359.9) == "N"


def test_ring_payload_preserves_validated_statistics() -> None:
    ring = RingStatistics(
        radius_km=25.0,
        valid_fraction=0.98,
        wet_pixel_count=7,
        maximum_mm_5min=0.42,
        wet_p90_mm_5min=0.31,
    )

    assert _ring_payload(ring) == {
        "radiusKm": 25.0,
        "validFraction": 0.98,
        "wetPixelCount": 7,
        "maximumMm5Min": 0.42,
        "wetP90Mm5Min": 0.31,
    }


def test_frame_payload_exposes_browser_timeline_without_raw_arrays() -> None:
    frame = RadarFrameAnalysis(
        lead_minutes=35,
        valid_fraction_site=1.0,
        coverage_sufficient=True,
        rain_at_site=False,
        site_wet_pixel_count=0,
        site_maximum_mm_5min=0.0,
        nearest_component=RainComponent(
            pixel_count=12,
            area_km2=12.0,
            nearest_distance_km=42.25,
            nearest_bearing_deg=91.0,
            maximum_mm_5min=0.8,
        ),
        rings=(
            RingStatistics(
                radius_km=10.0,
                valid_fraction=1.0,
                wet_pixel_count=0,
                maximum_mm_5min=0.0,
                wet_p90_mm_5min=None,
            ),
        ),
    )

    payload = _frame_payload(frame)

    assert payload["leadMinutes"] == 35
    assert payload["coverageSufficient"] is True
    assert payload["rainAtSite"] is False
    assert payload["siteIntensityMm5Min"] == 0.0
    assert payload["nearestDistanceKm"] == 42.25
    assert payload["nearestBearingDeg"] == 91.0
    assert payload["nearestDirection"] == "E"
    assert payload["componentAreaKm2"] == 12.0
    assert payload["componentMaximumMm5Min"] == 0.8
    assert payload["rings"][0]["radiusKm"] == 10.0
    assert "data" not in payload


def test_frame_payload_keeps_missing_component_explicit() -> None:
    frame = RadarFrameAnalysis(
        lead_minutes=0,
        valid_fraction_site=0.95,
        coverage_sufficient=True,
        rain_at_site=False,
        site_wet_pixel_count=0,
        site_maximum_mm_5min=None,
        nearest_component=None,
        rings=(),
    )

    payload = _frame_payload(frame)

    assert payload["nearestDistanceKm"] is None
    assert payload["nearestBearingDeg"] is None
    assert payload["nearestDirection"] is None
    assert payload["componentAreaKm2"] is None
    assert payload["componentMaximumMm5Min"] is None
    assert payload["rings"] == []


def test_optional_timeline_statistics_cannot_invalidate_safety_payload() -> None:
    frame = SimpleNamespace(
        lead_minutes=0,
        nearest_component=SimpleNamespace(
            nearest_distance_km=12.5,
            nearest_bearing_deg=91.0,
            area_km2=8.0,
        ),
        site_maximum_mm_5min=0.0,
    )

    payload = _frame_payload(frame)

    assert payload["leadMinutes"] == 0
    assert payload["nearestDistanceKm"] == 12.5
    assert payload["nearestDirection"] == "E"
    assert payload["componentMaximumMm5Min"] is None
    assert payload["coverageSufficient"] is None
    assert payload["rings"] == []
