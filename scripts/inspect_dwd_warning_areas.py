"""Inspect the official DWD warning-area WFS around Geiselhoering."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from shapely.geometry import Point, shape

WFS_URL = "https://maps.dwd.de/geoserver/dwd/ows"
LAYER = "dwd:Warngebiete_Gemeinden"
LONGITUDE = 12.40
LATITUDE = 48.84


def json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return str(value)


async def run(output: Path) -> None:
    params = {
        "service": "WFS",
        "version": "1.0.0",
        "request": "GetFeature",
        "typeName": LAYER,
        "srsName": "EPSG:4326",
        "outputFormat": "application/json",
        "bbox": "12.39,48.83,12.41,48.85,EPSG:4326",
    }
    timeout = httpx.Timeout(connect=20, read=90, write=20, pool=20)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            WFS_URL,
            params=params,
            headers={"Accept": "application/json", "User-Agent": "AstroWolkencheck/4.3"},
            follow_redirects=False,
        )
    if response.status_code != 200:
        raise RuntimeError(f"DWD WFS returned HTTP {response.status_code}")
    if len(response.content) > 20 * 1024 * 1024:
        raise RuntimeError("DWD WFS response exceeds the safety limit")
    if "json" not in response.headers.get("content-type", "").casefold():
        raise RuntimeError("DWD WFS did not return JSON")
    payload = response.json()
    features = payload.get("features")
    if payload.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise RuntimeError("DWD WFS response is not a FeatureCollection")
    if not features:
        raise RuntimeError("DWD WFS returned no warning-area features")

    site = Point(LONGITUDE, LATITUDE)
    inspected: list[dict[str, Any]] = []
    containing = 0
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
            raise RuntimeError("DWD WFS returned an invalid feature")
        geometry = shape(feature["geometry"])
        if geometry.is_empty or not geometry.is_valid:
            raise RuntimeError("DWD WFS returned invalid geometry")
        covers = geometry.covers(site)
        containing += int(covers)
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            raise RuntimeError("DWD WFS feature has no properties")
        inspected.append(
            {
                "id": feature.get("id"),
                "geometryType": geometry.geom_type,
                "coversConfiguredPoint": covers,
                "bounds": list(geometry.bounds),
                "propertyKeys": sorted(str(key) for key in properties),
                "properties": json_safe(properties),
            }
        )

    report = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "purpose": "warning-area schema verification; not a safety decision",
        "request": {"endpoint": WFS_URL, "layer": LAYER, "parameters": params},
        "response": {
            "contentType": response.headers.get("content-type"),
            "bytes": len(response.content),
            "featureCount": len(features),
            "featuresCoveringConfiguredPoint": containing,
            "crs": json_safe(payload.get("crs")),
            "features": inspected,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output.read_text(encoding="utf-8"))
    if containing < 1:
        raise SystemExit("No official warning area covers the configured point")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.output))


if __name__ == "__main__":
    main()
