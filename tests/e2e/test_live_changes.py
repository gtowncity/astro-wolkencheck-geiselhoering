import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Route, sync_playwright

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "nowcast_service" / "static" / "local-live-changes.js"


def change_summary() -> dict[str, object]:
    return {
        "hasPrevious": True,
        "previousSnapshotId": "previous",
        "currentSnapshotId": "current",
        "riskStateChanged": True,
        "previousRiskState": "GREEN",
        "currentRiskState": "YELLOW",
        "equipmentStateChanged": False,
        "warningsAdded": [
            {
                "id": "storm",
                "event": "GEWITTER",
                "headline": "Amtliche Warnung vor Gewitter",
                "severity": "Moderate",
            }
        ],
        "warningsRemoved": [],
        "radarDistanceDeltaKm": -12.0,
        "radarDistanceTrend": "CLOSER",
        "arrivalChanged": True,
        "currentArrivalMinutes": 35.0,
        "rainNowChanged": False,
        "sourceStateChanges": [
            {
                "sourceId": "DWD_CAP",
                "previous": "LIVE",
                "current": "STALE",
            }
        ],
        "meaningfulChangeCount": 5,
        "noMeaningfulChange": False,
    }


def test_server_history_replaces_and_survives_browser_change_copy() -> None:
    def route_request(route: Route) -> None:
        path = urlparse(route.request.url).path
        if path == "/api/v1/changes":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(change_summary()),
            )
        else:
            route.fulfill(status=404, body="not found")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.route("**/*", route_request)
        page.set_content(
            """
            <!doctype html>
            <html>
              <body>
                <section id="live-dashboard-root">
                  <ul id="awc-change-list">
                    <li>Nur im Browser gespeicherter Vergleich.</li>
                  </ul>
                </section>
              </body>
            </html>
            """
        )
        page.add_script_tag(
            content="""
            window.AstroWolkencheckLiveDashboard = {
              getState() {
                return {
                  snapshot: {
                    snapshotId: 'current',
                    hardwareRisk: {dataQuality: 'COMPLETE'},
                  },
                };
              },
            };
            """
        )
        page.add_script_tag(content=SCRIPT.read_text(encoding="utf-8"))
        page.locator("#awc-change-list[data-source='SERVER_HISTORY']").wait_for()

        copy = page.locator("#awc-change-list").inner_text()
        assert "GREEN → YELLOW" in copy
        assert "12 km näher" in copy
        assert "Ankunftsfenster in etwa 35 Minuten" in copy
        assert "Neu: Amtliche Warnung vor Gewitter" in copy
        assert "Amtliche DWD-Warnungen: LIVE → STALE" in copy
        assert "Nur im Browser" not in copy

        page.evaluate(
            """
            const list = document.getElementById('awc-change-list');
            const item = document.createElement('li');
            item.textContent = 'Späterer Browser-Vergleich überschreibt die Historie.';
            list.replaceChildren(item);
            """
        )
        page.wait_for_function(
            "document.getElementById('awc-change-list').innerText.includes('GREEN → YELLOW')"
        )
        assert "Späterer Browser-Vergleich" not in page.locator(
            "#awc-change-list"
        ).inner_text()
        browser.close()
