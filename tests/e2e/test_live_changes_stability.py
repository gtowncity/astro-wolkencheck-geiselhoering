from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "nowcast_service" / "static" / "local-live-changes.js"


def test_live_changes_does_not_create_a_mutation_feedback_loop() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content(
            """<!doctype html>
<html lang='de'>
<head><meta charset='utf-8'><base href='http://awc.test/'></head>
<body>
  <section id='live-dashboard-root'>
    <article class='awc-change-card'>
      <div class='awc-card-head'><p id='awc-change-summary'>Wird geladen</p></div>
      <ul id='awc-change-list'></ul>
      <div id='unrelated-live-churn'></div>
    </article>
  </section>
</body>
</html>"""
        )
        page.evaluate(
            """
            window.__awcFetchCount = 0;
            window.__awcHeartbeat = 0;
            window.AstroWolkencheckLiveDashboard = {
              getState: () => ({
                snapshot: {
                  snapshotId: 'snapshot-1',
                  hardwareRisk: { dataQuality: 'COMPLETE' },
                },
              }),
            };
            window.fetch = async () => {
              window.__awcFetchCount += 1;
              return {
                ok: true,
                json: async () => ({
                  hasPrevious: true,
                  currentSnapshotId: 'snapshot-1',
                  meaningfulChangeCount: 0,
                  warningsAdded: [],
                  warningsRemoved: [],
                  sourceStateChanges: [],
                }),
              };
            };
            window.setInterval(() => { window.__awcHeartbeat += 1; }, 20);
            """
        )
        page.add_script_tag(content=SCRIPT.read_text(encoding="utf-8"))
        page.wait_for_function(
            "document.querySelectorAll('#awc-change-list li').length === 1",
            timeout=2000,
        )
        first_heartbeat = page.evaluate("window.__awcHeartbeat")
        page.evaluate(
            """
            const target = document.getElementById('unrelated-live-churn');
            for (let index = 0; index < 1000; index += 1) {
              target.textContent = `update-${index}`;
            }
            """
        )
        page.wait_for_timeout(400)
        second_heartbeat = page.evaluate("window.__awcHeartbeat")
        assert second_heartbeat - first_heartbeat >= 5
        assert page.evaluate("window.__awcFetchCount") == 1
        assert page.locator("#awc-change-summary").inner_text().startswith(
            "Keine sicherheitsrelevante Veränderung"
        )
        browser.close()
