from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "nowcast_service" / "static"


def html_shell() -> str:
    buttons = "".join(
        f"<button class='tab-button{' active' if tab == 'overview' else ''}' "
        f"data-tab='{tab}'>{label}</button>"
        for tab, label in (
            ("overview", "Übersicht"),
            ("windows", "Beste Zeiten"),
            ("hours", "Stunden"),
            ("data", "Daten"),
            ("nowcast", "Vor dem Aufbau prüfen"),
        )
    )
    options = "".join(
        f"<option value='{tab}'>{label}</option>"
        for tab, label in (
            ("overview", "Übersicht"),
            ("windows", "Beste Zeiten"),
            ("hours", "Stunden"),
            ("data", "Daten"),
            ("nowcast", "Vor dem Aufbau prüfen"),
        )
    )
    return f"""<!doctype html>
<html lang='de'>
<head>
  <meta charset='utf-8'>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #080d13; color: #edf3f8; }}
    .app {{ width: min(1160px, calc(100% - 24px)); margin: 0 auto; }}
    .tabs {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .tab-panel {{ display: none; min-width: 0; }}
    .tab-panel.active {{ display: block; }}
    #live-dashboard-root {{ min-width: 0; padding: 12px; }}
  </style>
  <link rel='stylesheet' href='/local-live-navigation.css'>
</head>
<body>
  <div class='app'>
    <div id='live-dashboard-root'>Live-Cockpit</div>
    <div class='tabs-shell'>
      <label>Bereich<select id='mobileTabSelect'>{options}</select></label>
      <nav class='tabs'>{buttons}</nav>
    </div>
    <main>
      <section id='overview' class='tab-panel active'>
        <div id='overviewContent'>Nachtplanung</div>
      </section>
      <section id='windows' class='tab-panel'>
        <div class='panel'><div id='windowsContent'>Beste Zeiten</div></div>
      </section>
      <section id='hours' class='tab-panel'>Stunden</section>
      <section id='data' class='tab-panel'>Daten</section>
      <section id='nowcast' class='tab-panel'>Alter Nowcast</section>
    </main>
  </div>
</body>
</html>"""


def test_tablist_keyboard_semantics_and_responsive_widths() -> None:
    script = (STATIC / "local-live-navigation.js").read_text(encoding="utf-8")
    style = (STATIC / "local-live-navigation.css").read_text(encoding="utf-8")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1024, "height": 768})
        page.set_content(html_shell())
        page.add_style_tag(content=style)
        page.add_script_tag(content=script)
        page.locator("#awc-tab-nowcast").wait_for()

        tablist = page.locator(".tabs")
        assert tablist.get_attribute("role") == "tablist"
        assert tablist.get_attribute("aria-label") == "Bereich auswählen"

        now = page.locator("#awc-tab-nowcast")
        assert now.get_attribute("role") == "tab"
        assert now.get_attribute("aria-controls") == "nowcast"
        assert now.get_attribute("aria-selected") == "true"
        assert page.locator("#nowcast").get_attribute("role") == "tabpanel"
        assert page.locator("#nowcast").get_attribute("aria-labelledby") == (
            "awc-tab-nowcast"
        )

        now.focus()
        page.keyboard.press("ArrowRight")
        assert page.locator("#awc-tab-overview").get_attribute("aria-selected") == (
            "true"
        )
        assert page.locator("#overview").is_visible()
        assert page.evaluate("document.activeElement.id") == "awc-tab-overview"

        page.keyboard.press("End")
        assert page.locator("#awc-tab-data").get_attribute("aria-selected") == "true"
        assert page.evaluate("document.activeElement.id") == "awc-tab-data"

        page.keyboard.press("Home")
        assert now.get_attribute("aria-selected") == "true"
        assert page.evaluate("document.activeElement.id") == "awc-tab-nowcast"

        no_overflow = (
            "document.documentElement.scrollWidth <= "
            "document.documentElement.clientWidth"
        )
        for width, height in (
            (360, 800),
            (390, 844),
            (768, 1024),
            (1024, 768),
            (1280, 800),
            (1440, 900),
            (1920, 1080),
        ):
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate(no_overflow), f"overflow at {width}x{height}"
            assert page.locator("#nowcast").is_visible()
        browser.close()
