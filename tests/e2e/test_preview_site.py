from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.e2e

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREVIEW_INDEX = PROJECT_ROOT / "preview" / "index.html"


def test_preview_is_labeled_and_switches_unified_dashboard_states() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1165, "height": 768})
        page.goto(PREVIEW_INDEX.as_uri(), wait_until="domcontentloaded")

        dashboard = page.locator("#live-dashboard-root")
        axis = page.locator("#awc-live-axis")
        dashboard.wait_for()
        page.wait_for_function(
            "document.getElementById('awc-api-pill')?.dataset.connection === 'CONNECTED'"
        )

        banner_text = page.locator("#preview-banner").inner_text()
        assert "DEMO / VISUELLE VORSCHAU" in banner_text
        assert "Keine Live-Sicherheitsdaten" in banner_text
        assert axis.get_attribute("data-tone") == "GREEN"
        assert page.locator("#forecast-preview iframe").count() == 1
        assert page.locator("#awc-timeline-path").count() == 1
        assert "25 validierte Radarframes" in page.locator(
            "#awc-timeline-detail"
        ).inner_text()

        page.locator("[data-preview-state='YELLOW']").click()
        page.wait_for_function(
            "document.getElementById('awc-live-axis')?.dataset.tone === 'YELLOW'"
        )
        assert "Niederschlag nähert sich" in dashboard.inner_text()
        assert page.locator("[data-preview-state='YELLOW']").get_attribute(
            "aria-pressed"
        ) == "true"

        page.locator("[data-preview-state='RED']").click()
        page.wait_for_function(
            "document.getElementById('awc-live-axis')?.dataset.tone === 'RED'"
        )
        assert "Amtliche Warnung vor Gewitter" in page.locator(
            "#awc-warning-list"
        ).inner_text()
        assert page.locator("#awc-hazard-card").is_visible()
        assert "Gewitter" in page.locator("#awc-hazard-list").inner_text()

        page.locator("[data-preview-state='UNKNOWN']").click()
        page.wait_for_function(
            "document.getElementById('awc-live-axis')?.dataset.tone === 'UNKNOWN'"
        )
        assert "Radarquelle ist ausgefallen" in dashboard.inner_text()

        page.locator("[data-preview-state='GREEN']").click()
        page.wait_for_function(
            "document.getElementById('awc-live-axis')?.dataset.tone === 'GREEN'"
        )
        page.locator("#preview-disconnect").click()
        page.wait_for_function(
            "document.getElementById('awc-api-pill')?.dataset.connection === 'DISCONNECTED'"
        )
        assert axis.get_attribute("data-tone") == "UNKNOWN"
        assert "unterbrochen" in page.locator(
            "#awc-urgent-announcement"
        ).inner_text().lower()

        no_overflow = (
            "document.documentElement.scrollWidth <= "
            "document.documentElement.clientWidth"
        )
        assert page.evaluate(no_overflow)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate(no_overflow)
        assert dashboard.is_visible()

        browser.close()
