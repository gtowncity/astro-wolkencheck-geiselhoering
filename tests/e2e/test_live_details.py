import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, Route, sync_playwright

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "nowcast_service" / "static"


def warning_payload(*, red: bool) -> dict[str, object]:
    return {
        "identifier": "storm" if red else "heat",
        "event": "SCHWERES GEWITTER" if red else "HITZE",
        "headline": (
            "Amtliche Warnung vor schwerem Gewitter" if red else "Warnung vor Hitze"
        ),
        "severity": "Severe" if red else "Moderate",
        "urgency": "Immediate" if red else "Expected",
        "certainty": "Observed" if red else "Likely",
        "sent": "2026-08-05T08:00:00Z",
        "effective": "2026-08-05T08:15:00Z",
        "onset": "2026-08-05T09:00:00Z",
        "expires": "2026-08-05T12:00:00Z",
        "senderName": "Deutscher Wetterdienst",
        "description": "Amtliche Beschreibung.",
        "instruction": "Amtliche Verhaltensempfehlung.",
    }


def source_payload(
    source_id: str,
    *,
    stale_after: int,
    invalid_after: int,
    payload: dict[str, object],
) -> dict[str, object]:
    return {
        "sourceId": source_id,
        "state": "LIVE",
        "ageSeconds": 60,
        "isComplete": True,
        "staleAfterSeconds": stale_after,
        "invalidAfterSeconds": invalid_after,
        "payload": payload,
    }


def make_snapshot(*, red: bool = False) -> dict[str, object]:
    hazards: list[dict[str, object]] = []
    if red:
        hazards.append(
            {
                "hazardKey": "DWD_CAP:CAP_RELEVANT_WARNING_RED",
                "source": "DWD_CAP",
                "reason": "Schweres Gewitter bleibt vorsorglich aktiv.",
                "lastConfirmedAt": "2026-08-05T09:00:00Z",
                "holdUntil": "2026-08-05T09:30:00Z",
                "clearStreak": 1,
                "clearCyclesRequired": 2,
                "clearCondition": (
                    "Zwei frische vollständige Warnungszyklen ohne Gewitter."
                ),
            }
        )
    return {
        "snapshotId": "red" if red else "green",
        "evaluationAt": "2026-08-05T09:00:00Z",
        "equipmentState": "DEPLOYED_UNATTENDED",
        "hardwareRisk": {
            "state": "RED" if red else "GREEN",
            "dataQuality": "COMPLETE",
            "action": "COVER_EQUIPMENT_NOW" if red else "NO_LIVE_VETO_DETECTED",
            "reasonCodes": ["CAP_RELEVANT_WARNING_RED"] if red else [],
            "reasons": ["Schweres Gewitter am Standort."] if red else [],
        },
        "activeHazards": hazards,
        "sourceStates": {
            "DWD_RV": "LIVE",
            "DWD_CAP": "LIVE",
            "LOCAL_PERSISTENCE": "LIVE",
        },
        "sources": [
            source_payload(
                "DWD_RV",
                stale_after=900,
                invalid_after=1800,
                payload={
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
            source_payload(
                "DWD_CAP",
                stale_after=1200,
                invalid_after=2700,
                payload={"active": [warning_payload(red=red)]},
            ),
            source_payload(
                "LOCAL_PERSISTENCE",
                stale_after=86400,
                invalid_after=172800,
                payload={},
            ),
        ],
    }


def html_shell() -> str:
    return """<!doctype html>
<html>
<head>
  <meta charset='utf-8'>
  <title>Forecast</title>
  <link rel='stylesheet' href='/local-live.css'>
  <link rel='stylesheet' href='/local-live-details.css'>
</head>
<body>
  <main class='app'>
    <header class='app-header'>
      <div class='brand-block'><h1>Astro-Wolkencheck</h1></div>
    </header>
    <nav class='tabs-shell'>
      <button class='tab-button' data-tab='overview'>Übersicht</button>
      <select id='mobileTabSelect'><option value='overview'>Übersicht</option></select>
    </nav>
    <section id='overviewContent'>
      <article class='decision-hero good'><h2>Gute Chance</h2></article>
    </section>
  </main>
  <script src='/local-live.js' defer></script>
  <script src='/local-live-details.js' defer></script>
</body>
</html>"""


def install_browser_doubles(page: Page) -> None:
    page.add_init_script(
        """
        window.__notifications = [];
        class FakeNotification {
          static permission = 'granted';
          static async requestPermission() { return 'granted'; }
          constructor(title, options) { window.__notifications.push({title, options}); }
        }
        Object.defineProperty(window, 'Notification', {
          value: FakeNotification,
          configurable: true,
        });
        class FakeEventSource {
          constructor() {
            this.listeners = {};
            window.__events = this;
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


def fulfill_route(route: Route, current: dict[str, object]) -> None:
    path = urlparse(route.request.url).path
    assets = {
        "/local-live.js": ("text/javascript", STATIC / "local-live.js"),
        "/local-live.css": ("text/css", STATIC / "local-live.css"),
        "/local-live-details.js": (
            "text/javascript",
            STATIC / "local-live-details.js",
        ),
        "/local-live-details.css": (
            "text/css",
            STATIC / "local-live-details.css",
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
                    "browserNotificationsEnabled": True,
                }
            ),
        )
    elif path == "/api/v1/safety":
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(current["snapshot"]),
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
            body=json.dumps({"token": "x"}),
        )


def test_warning_latch_alarm_and_accessible_announcements() -> None:
    current: dict[str, object] = {"snapshot": make_snapshot()}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        install_browser_doubles(page)
        page.route("**/*", lambda route: fulfill_route(route, current))
        page.goto("http://awc.test/")
        page.locator(".awc-warning-facts").wait_for(state="attached")
        page.locator("#awc-warning-list details summary").first.click()
        page.locator(".awc-warning-facts").wait_for(state="visible")

        urgent = page.locator("#awc-urgent-announcement")
        polite = page.locator("#awc-polite-announcement")
        assert urgent.get_attribute("role") == "alert"
        assert urgent.get_attribute("aria-atomic") == "true"
        assert polite.get_attribute("role") == "status"
        assert polite.get_attribute("aria-live") == "polite"
        assert page.locator("#awc-action-card").get_attribute("aria-live") == "off"

        warning_text = page.locator(".awc-warning-facts").inner_text()
        assert "Dringlichkeit" in warning_text
        assert "erwartet" in warning_text
        assert "Sicherheit" in warning_text
        assert "wahrscheinlich" in warning_text
        assert "Deutscher Wetterdienst" in warning_text
        assert page.locator("#awc-alarm-capabilities").is_visible()
        assert page.locator("#awc-test-notification").is_visible()
        page.locator("#awc-test-notification").click()
        page.wait_for_function("window.__notifications.length === 1")

        page.evaluate(
            "payload => window.__events.emit('snapshot', payload)",
            make_snapshot(red=True),
        )
        page.locator(".awc-hazard-entry").wait_for()
        page.wait_for_function(
            "document.getElementById('awc-urgent-announcement').innerText.includes('Rot')"
        )
        assert "Ausrüstung sofort schützen" in urgent.inner_text()

        hazard_text = page.locator(".awc-hazard-entry").inner_text()
        assert "1 von 2 frischen Zyklen" in hazard_text
        assert "Mindesthaltezeit" in hazard_text
        assert "Quittiert" in hazard_text
        assert "niemals die erkannte Gefahr" in page.locator(
            "#awc-ack-note"
        ).inner_text()

        page.evaluate("window.__events.emit('error', new Event('error'))")
        page.wait_for_function(
            "document.getElementById('awc-urgent-announcement').innerText.includes('unterbrochen')"
        )
        assert "Sicherheitslage unbekannt" in urgent.inner_text()

        no_overflow = (
            "document.documentElement.scrollWidth <= "
            "document.documentElement.clientWidth"
        )
        assert page.evaluate(no_overflow)
        browser.close()
