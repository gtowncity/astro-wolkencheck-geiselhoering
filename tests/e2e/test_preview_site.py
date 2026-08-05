from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.e2e

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREVIEW_INDEX = PROJECT_ROOT / "preview" / "index.html"


def test_preview_is_labeled_and_switches_safety_states() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(PREVIEW_INDEX.as_uri(), wait_until="domcontentloaded")

        panel = page.locator("#awc-live-panel")
        panel.wait_for()
        assert "DEMO / VISUELLE VORSCHAU" in page.locator("#preview-banner").inner_text()
        assert "Keine Live-Sicherheitsdaten" in page.locator("#preview-banner").inner_text()
        assert panel.get_attribute("data-state") == "GREEN"
        assert page.locator("#forecast-preview iframe").count() == 1

        page.locator("[data-preview-state='YELLOW']").click()
        assert panel.get_attribute("data-state") == "YELLOW"
        assert "Niederschlag nähert sich" in page.locator("#awc-reasons").inner_text()

        page.locator("[data-preview-state='RED']").click()
        assert panel.get_attribute("data-state") == "RED"
        assert "Amtliche Warnung vor Gewitter" in page.locator("#awc-warnings").inner_text()
        assert "CAP_RELEVANT_WARNING_RED" in page.locator("#awc-hazards").inner_text()

        page.locator("[data-preview-state='UNKNOWN']").click()
        assert panel.get_attribute("data-state") == "UNKNOWN"
        assert "Green ist gesperrt" in page.locator("#awc-reasons").inner_text()

        page.locator("[data-preview-state='GREEN']").click()
        assert panel.get_attribute("data-state") == "GREEN"
        page.locator("#preview-disconnect").click()
        assert panel.get_attribute("data-state") == "UNKNOWN"
        assert "unterbrochen" in page.locator("#awc-connection").inner_text().lower()

        browser.close()
