import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pytest
from PIL import Image
from playwright.sync_api import Route, sync_playwright

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "nowcast_service" / "static" / "local-satellite-viewer.js"


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (120, 80), (30, 60, 90)).save(output, format="PNG")
    return output.getvalue()


def test_satellite_viewer_waits_for_deliberate_forecast_start() -> None:
    latest = datetime.now(UTC).replace(second=0, microsecond=0)
    frames = [
        (latest - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
        latest.isoformat().replace("+00:00", "Z"),
    ]
    errors: list[str] = []
    console: list[str] = []
    requests: list[str] = []

    def route_request(route: Route) -> None:
        requests.append(route.request.url)
        path = urlparse(route.request.url).path
        if path == "/":
            route.fulfill(
                status=200,
                content_type="text/html",
                body=(
                    "<!doctype html><html><head><meta charset='utf-8'></head>"
                    "<body><div class='awc-radar-visual'></div></body></html>"
                ),
            )
        elif path == "/api/v1/satellite/meta":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "provider": "EUMETSAT",
                        "satellite": "Meteosat-12",
                        "selectedProduct": {"key": "geocolour"},
                        "products": [
                            {
                                "key": "geocolour",
                                "title": "GeoColour",
                                "description": "Test",
                                "available": True,
                                "fresh": True,
                            }
                        ],
                        "frames": frames,
                        "freshnessLimitMinutes": 20,
                        "location": {
                            "name": "Geiselhoering",
                            "latitude": 48.84,
                            "longitude": 12.40,
                        },
                    }
                ),
            )
        elif path == "/api/v1/satellite/image":
            route.fulfill(status=200, content_type="image/png", body=png_bytes())
        else:
            route.fulfill(status=404, body="not found")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda message: console.append(message.text)
            if message.type in {"error", "warning"}
            else None,
        )
        page.route("**/*", route_request)
        page.goto("http://awc.test/")
        requests.clear()
        page.add_script_tag(content=SCRIPT.read_text(encoding="utf-8"))
        page.locator("#awc-satellite-viewer").wait_for(state="attached")
        page.wait_for_timeout(300)

        assert requests == []
        assert page.locator("#awc-satellite-status").inner_text() == (
            "Wartet auf Zeitraum, Ort und „Forecast laden“."
        )
        assert page.evaluate(
            "window.AstroWolkencheckSatelliteViewer.getState().enabled"
        ) is False

        page.evaluate(
            """
            window.dispatchEvent(new CustomEvent('awc:forecast-start', {
              detail: {
                location: {
                  name: 'Geiselhöring',
                  latitude: 48.84,
                  longitude: 12.40,
                },
                start: '2026-08-07T22:00',
                end: '2026-08-09T06:00',
              },
            }));
            """
        )
        page.wait_for_function(
            "document.getElementById('awc-satellite-image').complete && "
            "document.getElementById('awc-satellite-image').naturalWidth > 0"
        )

        assert any("/api/v1/satellite/meta" in url for url in requests)
        assert any("/api/v1/satellite/image" in url for url in requests)
        assert page.evaluate(
            "window.AstroWolkencheckSatelliteViewer.getState().enabled"
        ) is True
        assert errors == []
        assert console == []
        browser.close()
