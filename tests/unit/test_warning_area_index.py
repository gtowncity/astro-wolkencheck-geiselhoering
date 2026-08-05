import json
from datetime import UTC, datetime

import httpx
import pytest

from nowcast_service.sources.cap_parser import LocationMatch, parse_cap_xml
from nowcast_service.sources.warning_area_index import (
    DWD_WARNING_AREA_LAYER,
    DWD_WFS_ENDPOINT,
    DwdWarningAreaClient,
    WarningAreaError,
    parse_warning_area_geojson,
)

RETRIEVED_AT = datetime(2026, 8, 5, 7, 0, tzinfo=UTC)


def feature_collection(
    *,
    warncell_id: str | int = "808401123",
    name: str = "Stadt Geiselhöring",
    short_name_key: str = "SHORTNAME",
    geometry: dict[str, object] | None = None,
    crs_name: str = "urn:ogc:def:crs:OGC:1.3:CRS84",
) -> bytes:
    geometry = geometry or {
        "type": "Polygon",
        "coordinates": [
            [
                [12.30, 48.75],
                [12.50, 48.75],
                [12.50, 48.95],
                [12.30, 48.95],
                [12.30, 48.75],
            ]
        ],
    }
    return json.dumps(
        {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": crs_name}},
            "features": [
                {
                    "type": "Feature",
                    "id": "Warngebiete_Gemeinden.500006587",
                    "geometry": geometry,
                    "properties": {
                        "WARNCELLID": warncell_id,
                        "NAME": name,
                        short_name_key: "Geiselhöring",
                        "CONTACT": "Landratsamt Straubing-Bogen",
                    },
                }
            ],
        }
    ).encode()


def cap_with_geocode(value: str) -> bytes:
    return f"""<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
<identifier>id</identifier><sender>opendata@dwd.de</sender>
<sent>2026-08-05T06:30:00Z</sent><status>Actual</status>
<msgType>Alert</msgType><scope>Public</scope>
<info><language>de-DE</language><category>Met</category><event>GEWITTER</event>
<urgency>Immediate</urgency><severity>Severe</severity><certainty>Likely</certainty>
<expires>2026-08-05T12:00:00Z</expires>
<area><areaDesc>Test</areaDesc><geocode><valueName>WARNCELLID</valueName>
<value>{value}</value></geocode></area></info></alert>""".encode()


def test_verified_schema_parses_and_covers_geiselhoering() -> None:
    index = parse_warning_area_geojson(
        feature_collection(),
        retrieved_at=RETRIEVED_AT,
    )

    assert index.endpoint == DWD_WFS_ENDPOINT
    assert index.layer == DWD_WARNING_AREA_LAYER
    assert len(index.source_sha256) == 64
    assert index.ids_covering(12.40, 48.84) == ("808401123",)
    assert index.ids_covering(10.0, 48.0) == ()
    area = index.areas[0]
    assert area.name == "Stadt Geiselhöring"
    assert area.short_name == "Geiselhöring"


def test_current_numeric_dwd_schema_and_german_short_name_parse() -> None:
    index = parse_warning_area_geojson(
        feature_collection(warncell_id=809278123, short_name_key="KURZNAME"),
        retrieved_at=RETRIEVED_AT,
    )

    area = index.areas[0]
    assert area.warncell_id == "809278123"
    assert area.short_name == "Geiselhöring"
    assert index.ids_covering(12.40, 48.84) == ("809278123",)


def test_cap_geocode_resolves_site_match_foreign_nonmatch_and_uncovered_unknown() -> None:
    index = parse_warning_area_geojson(
        feature_collection(),
        retrieved_at=RETRIEVED_AT,
    )
    matching = parse_cap_xml(cap_with_geocode("808401123"))
    foreign = parse_cap_xml(cap_with_geocode("999999999"))
    info = matching.german_info()
    foreign_info = foreign.german_info()

    assert info is not None and foreign_info is not None
    assert (
        index.match(
            alert=matching,
            info=info,
            longitude=12.40,
            latitude=48.84,
        )
        is LocationMatch.MATCH
    )
    assert (
        index.match(
            alert=foreign,
            info=foreign_info,
            longitude=12.40,
            latitude=48.84,
        )
        is LocationMatch.NO_MATCH
    )
    assert (
        index.match(
            alert=matching,
            info=info,
            longitude=10.0,
            latitude=48.0,
        )
        is LocationMatch.UNKNOWN
    )


def test_empty_geocode_set_is_unknown() -> None:
    index = parse_warning_area_geojson(
        feature_collection(),
        retrieved_at=RETRIEVED_AT,
    )

    assert index.match_ids((), longitude=12.40, latitude=48.84) is LocationMatch.UNKNOWN


def test_invalid_schema_crs_geometry_identifiers_and_duplicates_are_rejected() -> None:
    with pytest.raises(WarningAreaError, match="CRS"):
        parse_warning_area_geojson(
            feature_collection(crs_name="EPSG:3857"),
            retrieved_at=RETRIEVED_AT,
        )
    with pytest.raises(WarningAreaError, match="Unsupported warning-area geometry"):
        parse_warning_area_geojson(
            feature_collection(geometry={"type": "Point", "coordinates": [12.4, 48.84]}),
            retrieved_at=RETRIEVED_AT,
        )
    for invalid_identifier in (True, 12.5, "not-a-number"):
        with pytest.raises(WarningAreaError, match="WARNCELLID"):
            parse_warning_area_geojson(
                feature_collection(warncell_id=invalid_identifier),
                retrieved_at=RETRIEVED_AT,
            )
    duplicate = json.loads(feature_collection())
    duplicate["features"].append(duplicate["features"][0])
    with pytest.raises(WarningAreaError, match="Duplicate"):
        parse_warning_area_geojson(
            json.dumps(duplicate).encode(),
            retrieved_at=RETRIEVED_AT,
        )


def test_non_dwd_endpoint_and_bad_coordinates_are_rejected() -> None:
    with pytest.raises(WarningAreaError, match="approved"):
        parse_warning_area_geojson(
            feature_collection(),
            retrieved_at=RETRIEVED_AT,
            endpoint="https://evil.example/ows",
        )
    index = parse_warning_area_geojson(
        feature_collection(),
        retrieved_at=RETRIEVED_AT,
    )
    with pytest.raises(ValueError, match="outside"):
        index.ids_covering(200, 48)


@pytest.mark.asyncio
async def test_wfs_client_uses_fixed_query_and_requires_site_coverage() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "maps.dwd.de"
        assert request.url.params["typeName"] == DWD_WARNING_AREA_LAYER
        assert request.url.params["srsName"] == "EPSG:4326"
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=feature_collection(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        index = await DwdWarningAreaClient(client).fetch(
            longitude=12.40,
            latitude=48.84,
            retrieved_at=RETRIEVED_AT,
        )

    assert index.ids_covering(12.40, 48.84) == ("808401123",)


@pytest.mark.asyncio
async def test_wfs_client_rejects_http_content_type_and_missing_coverage() -> None:
    responses = iter(
        [
            httpx.Response(503),
            httpx.Response(200, headers={"content-type": "text/html"}, text="x"),
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=feature_collection(
                    geometry={
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [10.0, 47.0],
                                [10.2, 47.0],
                                [10.2, 47.2],
                                [10.0, 47.2],
                                [10.0, 47.0],
                            ]
                        ],
                    }
                ),
            ),
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DwdWarningAreaClient(client)
        with pytest.raises(WarningAreaError, match="503"):
            await source.fetch(
                longitude=12.40,
                latitude=48.84,
                retrieved_at=RETRIEVED_AT,
            )
        with pytest.raises(WarningAreaError, match="JSON"):
            await source.fetch(
                longitude=12.40,
                latitude=48.84,
                retrieved_at=RETRIEVED_AT,
            )
        with pytest.raises(WarningAreaError, match="covers"):
            await source.fetch(
                longitude=12.40,
                latitude=48.84,
                retrieved_at=RETRIEVED_AT,
            )
