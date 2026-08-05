import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, Route, sync_playwright

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "nowcast_service" / "static"


def source(source_id: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "sourceId": source_id,
        "state": "LIVE",
        "ageSeconds": 30,
        "isComplete": True,
        "staleAfterSeconds": 900,
        "invalidAfterSeconds": 1800,
        "payload": payload,
    }


def snapshot() -> dict[str, object]:
    return {
        "snapshotId": "navigation",
        "evaluationAt": "2026-08-05T09:00:00Z",
        "equipmentState": "NOT_DEPLOYED",
        "hardwareRisk": {
            "state": "GREEN",
            "dataQuality": "COMPLETE",
            "action": "NO_LIVE_VETO_DETECTED",
            "reasonCodes": [],
            "reasons": [],
        },
        "activeHazards": [],
        "sourceStates": {
            "DWD_RV": "LIVE",
            "DWD_CAP": "LIVE",
            "LOCAL_PERSISTENCE": "LIVE",
        },
        "sources": [
            source(
                "DWD_RV",
                {
                    "frameCount": 25,
                    "forecastHorizonMinutes": 120,
                    "rainNow": False,
                    "movingTowardSite": False,
                    "siteIntensityMm5Min": 0.0,
                    "peakIntensityMm5Min": 0.0,
                    "unit": "mm/5min",
                    "coverage0To120": True,
                },
            ),
            source("DWD_CAP", {"active": []}),
            source("LOCAL_PERSISTENCE", {}),
        ],
    }


def html_shell() -> str:
    return """<!doctype html>
<html lang='de'>
<head>
  <meta charset='utf-8'>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; background: #080d13; color: #edf3f8; }
    .app { width: min(1160px, calc(100% - 24px)); margin: 0 auto; }
    .app-header { padding: 12px; background: #0f1721; }
    .tabs-shell { margin: 10px 0; }
    .tabs { display: flex; gap: 5px; }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }
    .panel, .decision-hero { padding: 12px; background: #0f1721; }
    @media (max-width: 600px) { .tabs { display: grid; grid-template-columns: 1fr 1fr; } }
  </style>
  <link rel='stylesheet' href='/local-live.css'>
  <link rel='stylesheet' href='/local-live-navigation.css'>
</head>
<body>
  <div class='app'>
    <header class='app-header'>
      <div class='brand-block'>
        <h1>Astro-Wolkencheck</h1>
        <p class='location-line'>Geiselhöring</p>
      </div>
    </header>
    <div class='tabs-shell'>
      <label class='mobile-tab-nav'>
        <span>Bereich</span>
        <select id='mobileTabSelect'>
          <option value='overview'>Übersicht</option>
          <option value='windows'>Beste Zeiten</option>
          <option value='hours'>Stunden</option>
          <option value='data'>Daten</option>
          <option value='nowcast'>Vor dem Aufbau prüfen</option>
        </select>
      </label>
      <nav class='tabs'>
        <button class='tab-button active' data-tab='overview'>Übersicht</button>
        <button class='tab-button' data-tab='windows'>Beste Zeiten</button>
        <button class='tab-button' data-tab='hours'>Stunden</button>
        <button class='tab-button' data-tab='data'>Daten</button>
        <button class='tab-button' data-tab='nowcast'>Vor dem Aufbau prüfen</button>
      </nav>
    </div>
    <main>
      <section id='overview' class='tab-panel active'>
        <div id='overviewContent'>
          <article class='decision-hero warn'>
            <h2>Nur unter Vorbehalt</h2>
            <div class='decision-facts'>
              <div><span>Größtes Risiko</span><strong>Wolken nehmen zu</strong></div>
              <div><span>Sicherheit</span><strong>mittel · 62/100</strong></div>
            </div>
          </article>
        </div>
        <div id='overviewCharts'>Forecast-Diagramme</div>
      </section>
      <section id='windows' class='tab-panel'>
        <div class='panel'>
          <h2>Beste zusammenhängende Zeiten</h2>
          <div id='windowsContent'>Samstag 23:00-04:00 Uhr</div>
        </div>
      </section>
      <section id='hours' class='tab-panel'><div class='panel'>Stunden</div></section>
      <section id='data' class='tab-panel'><div class='panel'>Daten</div></section>
      <section id='nowcast' class='tab-panel'>
        <div class='panel' id='obsolete-nowcast'>
          Automatisches Laden von DWD-Warnungen und Radar ist nicht eingebaut.
        </div>
      </section>
    </main>
  </div>
  <script src='/local-live.js' defer></script>
  <script src='/local-live-navigation.js' defer></script>
</body>
</html>"""


def install_event_source(page: Page) -> None:
    page.add_init_script(
        """
        class FakeEventSource {
          constructor() {
            this.listeners = {};
            setTimeout(() => this.emit('open', {}), 0);
          }
          addEventListener(type, callback) {
            (this.listeners[type] ||= []).push(callback);
          }
          emit(type, payload) {
            const event = ['open', 'error'].includes(type)
              ? payload
              : {data: JSON.stringify({payload})};
            for (const callback of this.listeners[type] || []) callback(event);
          }
          close() {}
        }
        Object.defineProperty(window, 'EventSource', {
          value: FakeEventSource,
          configurable: true,
        });
        """
    )


def route_request(route: Route) -> None:
    path = urlparse(route.request.url).path
    assets = {
        "/local-live.js": ("text/javascript", STATIC / "local-live.js"),
        "/local-live.css": ("text/css", STATIC / "local-live.css"),
        "/local-live-navigation.js": (
            "text/javascript",
            STATIC / "local-live-navigation.js",
        ),
        "/local-live-navigation.css": (
            "text/css",
            STATIC / "local-live-navigation.css",
        ),
    }
    if path == "/":
        route.fulfill(status=200, content_type="text/html", body=html_shell())
    elif path in assets:
        content_type, file_path = assets[path]
        route.fulfill(
            status=200,
            content_type=content_type,
            body=file_path.read_text(encoding="utf-8"),
        )
    elif path == "/runtime-config.json":
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "browserAudioEnabled": False,
                    "browserNotificationsEnabled": False,
                }
            ),
        )
    elif path == "/api/v1/safety":
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(snapshot()),
        )
    elif path == "/api/v1/alerts":
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"active": None}),
        )
    else:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"token": "csrf"}),
        )


def test_live_dashboard_becomes_now_view_and_windows_join_planning() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1165, "height": 768})
        install_event_source(page)
        page.route("**/*", route_request)
        page.goto("http://awc.test/")
        page.locator("#nowcast #live-dashboard-root").wait_for()

        labels = page.locator(".tabs .tab-button").all_inner_texts()
        assert labels == ["JETZT", "NACHT PLANEN", "STUNDEN", "DATEN & DIAGNOSE"]
        assert page.locator(".tab-button[data-tab='windows']").count() == 0
        assert page.locator("#obsolete-nowcast").count() == 0
        parent_id = page.locator("#live-dashboard-root").evaluate(
            "node => node.parentElement.id"
        )
        assert parent_id == "nowcast"
        assert page.locator("#nowcast").is_visible()
        assert page.locator("#overview").is_hidden()
        assert page.locator("#windowsContent").count() == 1
        assert page.locator("#overview .awc-planning-windows #windowsContent").count() == 1

        page.locator(".tab-button[data-tab='overview']").click()
        assert page.locator("#overview").is_visible()
        assert page.locator("#nowcast").is_hidden()
        assert page.locator("#overviewCharts").is_visible()
        assert page.locator("#windowsContent").is_visible()

        page.set_viewport_size({"width": 390, "height": 844})
        no_overflow = (
            "document.documentElement.scrollWidth <= "
            "document.documentElement.clientWidth"
        )
        assert page.evaluate(no_overflow)
        select = page.locator("#mobileTabSelect")
        assert select.locator("option").all_inner_texts() == [
            "NACHT PLANEN",
            "STUNDEN",
            "DATEN & DIAGNOSE",
            "JETZT",
        ]
        select.select_option("nowcast")
        assert page.locator("#nowcast").is_visible()
        assert page.locator("#live-dashboard-root").is_visible()
        browser.close()
