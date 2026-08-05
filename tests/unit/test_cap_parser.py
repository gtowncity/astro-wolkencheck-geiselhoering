from datetime import UTC, datetime

import pytest

from nowcast_service.sources.cap_parser import (
    CapParseError,
    LocationMatch,
    parse_cap_xml,
    resolve_cap_snapshot,
)

CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"


def cap_xml(
    *,
    identifier: str = "id-new",
    sent: str = "2026-08-05T06:30:00Z",
    message_type: str = "Alert",
    status: str = "Actual",
    scope: str = "Public",
    expires: str = "2026-08-05T12:00:00Z",
    references: str | None = None,
    geometry: str = "polygon",
) -> bytes:
    reference_xml = f"<references>{references}</references>" if references else ""
    if geometry == "polygon":
        area_geometry = (
            "<polygon>48.70,12.20 48.70,12.60 49.00,12.60 "
            "49.00,12.20 48.70,12.20</polygon>"
        )
    elif geometry == "circle":
        area_geometry = "<circle>48.84,12.40 5</circle>"
    elif geometry == "none":
        area_geometry = ""
    else:
        area_geometry = geometry
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="{CAP_NS}">
  <identifier>{identifier}</identifier>
  <sender>opendata@dwd.de</sender>
  <sent>{sent}</sent>
  <status>{status}</status>
  <msgType>{message_type}</msgType>
  <source>Deutscher Wetterdienst</source>
  <scope>{scope}</scope>
  <code>id:2.49.0.0.276.0.DWD.PVW</code>
  {reference_xml}
  <info>
    <language>de-DE</language>
    <category>Met</category>
    <event>STARKES GEWITTER</event>
    <responseType>Prepare</responseType>
    <urgency>Immediate</urgency>
    <severity>Severe</severity>
    <certainty>Likely</certainty>
    <effective>2026-08-05T06:00:00Z</effective>
    <onset>2026-08-05T06:15:00Z</onset>
    <expires>{expires}</expires>
    <senderName>Deutscher Wetterdienst</senderName>
    <headline>Amtliche Warnung vor starkem Gewitter</headline>
    <description>Amtlicher Beschreibungstext.</description>
    <instruction>Schutz suchen.</instruction>
    <eventCode><valueName>II</valueName><value>95</value></eventCode>
    <parameter>
      <valueName>warnVerwaltungsbereiche</valueName><value>09178</value>
    </parameter>
    <area>
      <areaDesc>Landkreis Straubing-Bogen</areaDesc>
      {area_geometry}
      <geocode>
        <valueName>WARNCELLID</valueName><value>109278000</value>
      </geocode>
      <altitude>0</altitude>
      <ceiling>30000</ceiling>
    </area>
  </info>
</alert>
""".encode()


def now() -> datetime:
    return datetime(2026, 8, 5, 7, 0, tzinfo=UTC)


def test_parses_official_fields_and_polygon_location() -> None:
    alert = parse_cap_xml(cap_xml())
    info = alert.german_info()

    assert alert.identifier == "id-new"
    assert alert.sender == "opendata@dwd.de"
    assert alert.message_type == "Alert"
    assert info is not None
    assert info.event == "STARKES GEWITTER"
    assert info.severity == "Severe"
    assert info.event_codes[0].name == "II"
    assert info.parameters[0].value == "09178"
    assert info.location_match(12.40, 48.84) is LocationMatch.MATCH
    assert info.location_match(10.00, 48.00) is LocationMatch.NO_MATCH
    assert info.is_in_force(now()) is True


def test_circle_and_unresolved_geocode_area_are_distinguished() -> None:
    circle = parse_cap_xml(cap_xml(geometry="circle")).german_info()
    unresolved = parse_cap_xml(cap_xml(geometry="none")).german_info()

    assert circle is not None and unresolved is not None
    assert circle.location_match(12.40, 48.84) is LocationMatch.MATCH
    assert circle.location_match(13.40, 48.84) is LocationMatch.NO_MATCH
    assert unresolved.location_match(12.40, 48.84) is LocationMatch.UNKNOWN


def test_expired_test_and_private_messages_are_not_displayable() -> None:
    expired = parse_cap_xml(cap_xml(expires="2026-08-05T06:59:00Z"))
    test_message = parse_cap_xml(cap_xml(status="Test"))
    private = parse_cap_xml(cap_xml(scope="Private"))

    assert expired.is_displayable(now()) is False
    assert test_message.is_displayable(now()) is False
    assert private.is_displayable(now()) is False


def test_update_supersedes_reference_and_cancel_removes_reference() -> None:
    old = parse_cap_xml(
        cap_xml(identifier="old", sent="2026-08-05T06:00:00Z")
    )
    update = parse_cap_xml(
        cap_xml(
            identifier="update",
            sent="2026-08-05T06:30:00Z",
            message_type="Update",
            references="opendata@dwd.de,old,2026-08-05T06:00:00Z",
        )
    )

    updated = resolve_cap_snapshot((old, update), now=now())

    assert [alert.identifier for alert in updated.active] == ["update"]
    assert updated.superseded_identifiers == ("old",)

    cancel = parse_cap_xml(
        cap_xml(
            identifier="cancel",
            sent="2026-08-05T06:45:00Z",
            message_type="Cancel",
            references="opendata@dwd.de,update,2026-08-05T06:30:00Z",
        )
    )
    cancelled = resolve_cap_snapshot((old, update, cancel), now=now())

    assert cancelled.active == ()
    assert "update" in cancelled.cancelled_identifiers


def test_latest_duplicate_identifier_wins() -> None:
    older = parse_cap_xml(
        cap_xml(identifier="same", sent="2026-08-05T06:00:00Z")
    )
    newer = parse_cap_xml(
        cap_xml(identifier="same", sent="2026-08-05T06:30:00Z")
    )

    snapshot = resolve_cap_snapshot((newer, older), now=now())

    assert len(snapshot.active) == 1
    assert snapshot.active[0].sent == datetime(2026, 8, 5, 6, 30, tzinfo=UTC)


def test_malformed_unsafe_namespace_and_time_are_rejected() -> None:
    malicious = (
        b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b"<a>&x;</a>"
    )
    with pytest.raises(CapParseError, match="malformed or unsafe"):
        parse_cap_xml(malicious)
    with pytest.raises(CapParseError, match="root element"):
        parse_cap_xml(b"<alert/>")
    with pytest.raises(CapParseError, match="timezone"):
        parse_cap_xml(cap_xml(sent="2026-08-05T06:30:00"))


def test_invalid_polygon_and_reference_are_rejected() -> None:
    bad_polygon = "<polygon>48,12 49,13 48,12</polygon>"
    with pytest.raises(CapParseError, match="closed ring"):
        parse_cap_xml(cap_xml(geometry=bad_polygon))
    with pytest.raises(CapParseError, match="references"):
        parse_cap_xml(cap_xml(references="not,a,valid,reference"))
