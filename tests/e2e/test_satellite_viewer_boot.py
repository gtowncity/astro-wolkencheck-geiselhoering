import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pytest
from PIL import Image
from playwright.sync_api import ConsoleMessage, Error, Route, sync_playwright

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "nowcast_service" / "static" / "local-satellite-viewer.js"


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (120, 80), (30, 60, 90)).save(output, format="PNG")
    return output.getvalue()


def test_satellite_viewer_boots_without_forecast_shell() -> None:
    latest = datetime.now(UTC).replace(second=0, microsecond=0)
    frames = [
        (latest - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
        latest.isoformat().replace("+00:00", "Z"),
    ]
    errors: list[str] = []
    console: list[str] = []

    def route_request(route: Route) -> None:
        path = urlparse(route.request.url).path
        if path == "/api/v1/satellite/meta":
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
        page.set_content(
            "<!doctype html><html><body>"
            "<div class='awc-radar-visual'></div>"
            "</body></html>"
        )
        page.add_script_tag(content=SCRIPT.read_text(encoding="utf-8"))
        page.wait_for_timeout(500)

        assert page.locator("#awc-satellite-image").count() == 1, (
            f"page errors={errors}; console={console}"
        )
        assert page.locator("#awc-satellite-image").evaluate(
            "node => node.complete && node.naturalWidth > 0"
        )
        browser.close()
