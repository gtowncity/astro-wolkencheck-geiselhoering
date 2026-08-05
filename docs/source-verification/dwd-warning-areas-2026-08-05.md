# DWD source verification: municipality warning-area WFS

Verification date: 2026-08-05 UTC

The isolated GitHub Actions smoke test queried the official DWD GeoServer WFS
around the configured Geiselhöring coordinate. This verification concerns only
the warning-area geometry and schema. It does not itself create a safety state.

## Verified endpoint and layer

```text
endpoint: https://maps.dwd.de/geoserver/dwd/ows
service: WFS
version: 1.0.0
request: GetFeature
layer: dwd:Warngebiete_Gemeinden
output: application/json
requested CRS: EPSG:4326
```

Workflow run:

```text
DWD Warning Area Live Smoke / run 30984152129
result: success
```

## Verified response around Geiselhöring

```text
response bytes: 6,676
feature count: 1
features covering 12.40, 48.84: 1
GeoJSON CRS: urn:ogc:def:crs:OGC:1.3:CRS84
geometry type: MultiPolygon
feature id: Warngebiete_Gemeinden.500006587
```

Observed properties:

```text
WARNCELLID: 808401123
NAME: Stadt Geiselhöring
SHORTNAME: Geiselhöring
CONTACT: Landratsamt Straubing-Bogen
```

The returned official geometry covered the configured approximate coordinate.
This confirms that CAP areas containing only an official warning-cell ID can be
resolved against the DWD municipality geometry instead of by text search.

## Implemented resolver rules

The warning-area index:

- uses only the fixed HTTPS DWD GeoServer endpoint,
- requests a small bounding box around the private configured location,
- requires a GeoJSON FeatureCollection,
- accepts only longitude/latitude CRS identifiers verified for the source,
- requires Polygon or MultiPolygon geometries,
- rejects empty, invalid, out-of-bounds and duplicate warning areas,
- requires `WARNCELLID` and `NAME`,
- uses exact geometry coverage for the configured point,
- returns `MATCH` when an official CAP warning-cell ID covers the point,
- returns `NO_MATCH` only when every supplied ID is known and none covers it,
- returns `UNKNOWN` when an ID cannot be resolved.

`UNKNOWN` is intentional: an unknown warning-cell mapping must never be treated
as absence of an official warning and therefore cannot support hardware GREEN.

## Remaining runtime gate

Before the CAP core source can become `LIVE`, the service still must:

- refresh and cache the official warning-area index atomically,
- apply source-specific freshness and publication-delay limits,
- load every CAP XML document in the status archive,
- resolve direct geometry first and warning-cell IDs second,
- reject or mark incomplete snapshots with unresolved relevant areas,
- latch previously active hazards across source failures,
- publish the completed CAP snapshot to the runtime decision engine.

## Official references

- DWD Open Data help and geoservice guidance:
  `https://www.dwd.de/DE/leistungen/opendata/hilfe.html`
- DWD warning-cell ID documentation:
  `https://www.dwd.de/DE/leistungen/opendata/help/warnungen/cap_warncellids.html`
- Official GeoServer WFS endpoint:
  `https://maps.dwd.de/geoserver/dwd/ows`
