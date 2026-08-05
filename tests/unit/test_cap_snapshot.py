import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nowcast_service.decision_engine import RiskState
from nowcast_service.sources.cap_parser import CapAlert, CapInfo, LocationMatch
from nowcast_service.sources.cap_snapshot import (
    CapArchiveError,
    cap_hazard_evidence,
    load_cap_archive,
)

CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"
NOW = datetime(2026, 8, 5, 7, 0, tzinfo=UTC)


def alert_xml(
    *,
    identifier: str,
    event: str = "STARKES GEWITTER",
    severity: str = "Severe",
    geometry: str = "polygon",
    expires: str = "2026-08-05T12:00:00Z",
) -> bytes:
    if geometry == "polygon":
        area = "<polygon>48.70,12.20 48.70,12.60 49.00,12.60 49.00,12.20 48.70,12.20</polygon>"
    elif geometry == "away":
        area = "<polygon>47.00,10.00 47.00,10.20 47.20,10.20 47.20,10.00 47.00,10.00</polygon>"
    elif geometry == "geocode":
        area = "<geocode><valueName>WARNCELLID</valueName><value>109278000</value></geocode>"
    else:
        raise AssertionError(geometry)
    return f"""<alert xmlns="{CAP_NS}">
<identifier>{identifier}</identifier>
<sender>opendata@dwd.de</sender>
<sent>2026-08-05T06:30:00Z</sent>
<status>Actual</status>
<msgType>Alert</msgType>
<scope>Public</scope>
<info>
<language>de-DE</language>
<category>Met</category>
<event>{event}</event>
<urgency>Immediate</urgency>
<severity>{severity}</severity>
<certainty>Likely</certainty>
<onset>2026-08-05T06:15:00Z</onset>
<expires>{expires}</expires>
<headline>Amtliche Warnung: {event}</headline>
<area><areaDesc>Testgebiet</areaDesc>{area}</area>
</info>
</alert>""".encode()


def make_archive(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, content in members.items():
            bundle.writestr(name, content)
    return path


class FixedResolver:
    def __init__(self, result: LocationMatch) -> None:
        self.result = result
        self.calls = 0

    def match(
        self,
        *,
        alert: CapAlert,
        info: CapInfo,
        longitude: float,
        latitude: float,
    ) -> LocationMatch:
        del alert, info, longitude, latitude
        self.calls += 1
        return self.result


def test_archive_requires_every_xml_member_to_parse(tmp_path: Path) -> None:
    archive = make_archive(
        tmp_path / "status.zip",
        {
            "valid.xml": alert_xml(identifier="valid"),
            "broken.xml": b"<not-cap/>",
        },
    )

    with pytest.raises(CapArchiveError, match=r"broken\.xml"):
        load_cap_archive(
            archive,
            now=NOW,
            longitude=12.40,
            latitude=48.84,
        )


def test_archive_hash_count_and_direct_location_result(tmp_path: Path) -> None:
    archive = make_archive(
        tmp_path / "status.zip",
        {
            "match.xml": alert_xml(identifier="match"),
            "away.xml": alert_xml(identifier="away", geometry="away"),
        },
    )

    result = load_cap_archive(
        archive,
        now=NOW,
        longitude=12.40,
        latitude=48.84,
    )

    assert result.source_file == "status.zip"
    assert len(result.source_sha256) == 64
    assert result.archive_entries == 2
    assert result.parsed_alerts == 2
    assert [item.identifier for item in result.location.matched] == ["match"]
    assert [item.identifier for item in result.location.nonmatching] == ["away"]
    assert result.location.unresolved == ()
    assert result.location.complete is True


def test_geocode_only_area_is_unknown_without_official_resolver(tmp_path: Path) -> None:
    archive = make_archive(
        tmp_path / "status.zip",
        {"coded.xml": alert_xml(identifier="coded", geometry="geocode")},
    )

    result = load_cap_archive(
        archive,
        now=NOW,
        longitude=12.40,
        latitude=48.84,
    )

    assert [item.identifier for item in result.location.unresolved] == ["coded"]
    assert result.location.complete is False


def test_official_resolver_can_match_or_reject_geocode_area(tmp_path: Path) -> None:
    archive = make_archive(
        tmp_path / "status.zip",
        {"coded.xml": alert_xml(identifier="coded", geometry="geocode")},
    )
    matching = FixedResolver(LocationMatch.MATCH)
    nonmatching = FixedResolver(LocationMatch.NO_MATCH)

    matched = load_cap_archive(
        archive,
        now=NOW,
        longitude=12.40,
        latitude=48.84,
        resolver=matching,
    )
    rejected = load_cap_archive(
        archive,
        now=NOW,
        longitude=12.40,
        latitude=48.84,
        resolver=nonmatching,
    )

    assert matching.calls == 1
    assert nonmatching.calls == 1
    assert [item.identifier for item in matched.location.matched] == ["coded"]
    assert [item.identifier for item in rejected.location.nonmatching] == ["coded"]


def test_severe_matched_weather_warning_is_red(tmp_path: Path) -> None:
    archive = make_archive(
        tmp_path / "status.zip",
        {"warning.xml": alert_xml(identifier="warning")},
    )
    result = load_cap_archive(
        archive,
        now=NOW,
        longitude=12.40,
        latitude=48.84,
    )

    evidence = cap_hazard_evidence(result)

    assert evidence[0].state is RiskState.RED
    assert evidence[0].reason_code == "CAP_RELEVANT_WARNING_RED"
    assert "Amtliche Warnung" in evidence[0].reason


def test_moderate_weather_warning_is_yellow_and_fog_is_not_hardware_hazard(
    tmp_path: Path,
) -> None:
    rain_archive = make_archive(
        tmp_path / "rain.zip",
        {
            "rain.xml": alert_xml(
                identifier="rain",
                event="STARKREGEN",
                severity="Moderate",
            )
        },
    )
    fog_archive = make_archive(
        tmp_path / "fog.zip",
        {"fog.xml": alert_xml(identifier="fog", event="NEBEL")},
    )

    rain = load_cap_archive(
        rain_archive,
        now=NOW,
        longitude=12.40,
        latitude=48.84,
    )
    fog = load_cap_archive(
        fog_archive,
        now=NOW,
        longitude=12.40,
        latitude=48.84,
    )

    assert cap_hazard_evidence(rain)[0].state is RiskState.YELLOW
    assert cap_hazard_evidence(fog) == ()


def test_expired_warning_does_not_become_location_or_hazard_evidence(tmp_path: Path) -> None:
    archive = make_archive(
        tmp_path / "expired.zip",
        {
            "expired.xml": alert_xml(
                identifier="expired",
                expires="2026-08-05T06:59:00Z",
            )
        },
    )

    result = load_cap_archive(
        archive,
        now=NOW,
        longitude=12.40,
        latitude=48.84,
    )

    assert result.snapshot.active == ()
    assert result.location.matched == ()
    assert cap_hazard_evidence(result) == ()


def test_invalid_zip_is_reported_as_archive_failure(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    archive.write_bytes(b"not a zip")

    with pytest.raises(CapArchiveError, match="validation failed"):
        load_cap_archive(
            archive,
            now=NOW,
            longitude=12.40,
            latitude=48.84,
        )
