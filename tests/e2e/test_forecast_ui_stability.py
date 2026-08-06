from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "nowcast_service" / "static" / "local-ui-recovery.js"


def html_shell() -> str:
    return """<!doctype html>
<html lang='de'>
<head><meta charset='utf-8'><base href='http://awc.test/'></head>
<body>
  <main class='app'>
    <header class='app-header'>
      <div class='controls'>
        <input id='startInput' type='datetime-local' value='2026-08-07T22:00'>
        <input id='endInput' type='datetime-local' value='2026-08-09T06:00'>
        <button id='refreshBtn' type='button'>Wetter neu laden</button>
      </div>
      <div id='requestProgress' hidden>
        <strong id='progressMetric'>0 von 16 Quellen verarbeitet</strong>
      </div>
      <div id='cacheNotice'></div>
    </header>
    <section id='overviewContent'></section>
    <section id='live-dashboard-root'>
      <div class='awc-live-grid'>
        <article class='awc-radar-card'>Radar</article>
        <div class='awc-side-stack'>
          <article class='awc-warnings-card'>Warnungen</article>
          <article class='awc-sources-card'>Quellen</article>
          <article class='awc-hazard-card'>Gefahr</article>
        </div>
      </div>
      <div class='awc-secondary-grid'>
        <article class='awc-change-card'>Änderungen</article>
        <article class='awc-alarm-card'>Alarm</article>
      </div>
    </section>
    <div id='unrelated-live-churn'></div>
  </main>
</body>
</html>"""


def test_forecast_recovery_ignores_unrelated_dom_churn() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content(html_shell())
        page.add_script_tag(content=SCRIPT.read_text(encoding="utf-8"))
        page.locator("#awc-forecast-placeholder-title").wait_for()
        page.wait_for_timeout(250)

        first = page.evaluate(
            "window.AstroWolkencheckForecastNetwork.getState().syncRuns"
        )
        page.evaluate(
            """
            const target = document.getElementById('unrelated-live-churn');
            for (let index = 0; index < 500; index += 1) {
              target.textContent = `live-${index}`;
              target.dataset.tone = index % 2 ? 'GREEN' : 'YELLOW';
            }
            """
        )
        page.wait_for_timeout(650)
        second = page.evaluate(
            "window.AstroWolkencheckForecastNetwork.getState().syncRuns"
        )
        assert second - first <= 1

        page.evaluate(
            """
            const progress = document.getElementById('requestProgress');
            progress.hidden = false;
            document.getElementById('progressMetric').textContent =
              '4 von 16 Quellen verarbeitet';
            """
        )
        page.locator("#awc-cancel-forecast").wait_for(state="visible")
        during = page.evaluate(
            "window.AstroWolkencheckForecastNetwork.getState().syncRuns"
        )
        page.wait_for_timeout(350)
        after = page.evaluate(
            "window.AstroWolkencheckForecastNetwork.getState().syncRuns"
        )
        assert after - during <= 1

        page.evaluate(
            "document.getElementById('requestProgress').hidden = true"
        )
        page.locator("#awc-cancel-forecast").wait_for(state="hidden")
        assert page.locator("#refreshBtn").is_enabled()
        browser.close()
