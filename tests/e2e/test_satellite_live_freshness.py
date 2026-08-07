import io
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from PIL import Image
from playwright.sync_api import Route, sync_playwright

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "nowcast_service" / "static"


def satellite_png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (120, 80), (24, 45, 66)).save(output, format="PNG")
    return output.getvalue()


def test_live_satellite_uses_verified_age_for_stale_status() -> None:
    metadata = {
        "provider": "EUMETSAT",
        "satellite": "Meteosat-12 / MTG-I1 / FCI",
        "selectedProduct": {"key": "cloudtype"},
        "products": [
            {
                "key": "cloudtype",
                "title": "Wolkentypen RGB",
                "description": "Wolkentypen",
                "available": True,
            }
        ],
        "frames": ["2026-08-07T06:40:00Z"],
        "freshnessLimitMinutes": 20,
        "location": {
            "name": "Geiselhoering",
            "latitude": 48.84,
            "longitude": 12.40,
        },
    }

    def route_request(route: Route) -> None:
        parsed = urlparse(route.request.url)
        if parsed.path == "/":
            route.fulfill(
                status=200,
                content_type="text/html",
                body=(
                    "<!doctype html><html><body>"
                    "<div class='awc-radar-visual'></div>"
                    "</body></html>"
                ),
            )
            return
        if parsed.path == "/api/v1/satellite/meta":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(metadata),
            )
            return
        if parsed.path == "/api/v1/satellite/image":
            route.fulfill(
                status=200,
                content_type="image/png",
                body=satellite_png(),
                headers={
                    "X-Satellite-Observation-Time": "2026-08-07T06:50:00Z",
                    "X-Satellite-Age-Minutes": "56.0",
                    "X-Satellite-Fresh": "false",
                },
            )
            return
        route.fulfill(status=404, body="not found")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.route("**/*", route_request)
        page.goto("http://awc.test/")
        page.add_script_tag(
            content=(STATIC / "local-satellite-viewer.js").read_text(encoding="utf-8")
        )
        page.locator("#awc-satellite-viewer").wait_for(state="attached")
        page.evaluate(
            """
            window.dispatchEvent(new CustomEvent('awc:forecast-start', {
              detail: {
                location: {
                  name: 'Geiselhoering',
                  latitude: 48.84,
                  longitude: 12.40,
                },
              },
            }));
            """
        )
        page.wait_for_function(
            "document.getElementById('awc-satellite-status').dataset.tone === 'STALE'"
        )

        status = page.locator("#awc-satellite-status").inner_text()
        time_text = page.locator("#awc-satellite-time").inner_text()
        image_src = page.locator("#awc-satellite-image").get_attribute("src") or ""

        assert status == "VERALTET · 56 Min. alt · Grenze 20 Min."
        assert "06:50" in time_text
        assert "56 Min. alt" in time_text
        assert image_src.startswith("blob:")
        browser.close()
