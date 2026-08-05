import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "nowcast_service" / "static" / "local-live-changes.js"

PAGE = """<!doctype html>
<html>
  <body>
    <section id="live-dashboard-root">
      <article class="awc-change-card">
        <div class="awc-card-head">
          <p id="awc-change-summary">Alter Browser-Vergleich.</p>
        </div>
        <ul id="awc-change-list">
          <li>Nur im Browser gespeicherter Vergleich.</li>
        </ul>
      </article>
    </section>
  </body>
</html>"""


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
    summary = change_summary()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content(PAGE)
        page.add_script_tag(
            content="""
            window.fetch = async () => new Response('{}', {status: 503});
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
        page.wait_for_function("Boolean(window.AstroWolkencheckLiveChanges)")
        page.evaluate(
            "summary => window.AstroWolkencheckLiveChanges.render(summary)",
            summary,
        )

        intro = page.locator("#awc-change-summary")
        assert intro.inner_text() == (
            "5 sicherheitsrelevante Änderungen seit dem vorherigen vollständigen "
            "Snapshot."
        )
        copy = page.locator("#awc-change-list").inner_text()
        assert "GREEN → YELLOW" in copy
        assert "12 km näher" in copy
        assert "Ankunftsfenster in etwa 35 Minuten" in copy
        assert "Neu: Amtliche Warnung vor Gewitter" in copy
        assert "Amtliche DWD-Warnungen: LIVE → STALE" in copy
        assert "Nur im Browser" not in copy

        page.evaluate(
            """
            document.getElementById('awc-change-summary').textContent =
              'Spätere widersprüchliche Einleitung.';
            const list = document.getElementById('awc-change-list');
            const item = document.createElement('li');
            item.textContent = 'Späterer Browser-Vergleich überschreibt die Historie.';
            list.replaceChildren(item);
            """
        )
        page.evaluate(
            "summary => window.AstroWolkencheckLiveChanges.render(summary)",
            summary,
        )
        assert "Späterer Browser-Vergleich" not in page.locator(
            "#awc-change-list"
        ).inner_text()
        assert "widersprüchliche" not in intro.inner_text()
        browser.close()
