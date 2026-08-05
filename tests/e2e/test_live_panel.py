import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, Route, sync_playwright

pytestmark = pytest.mark.e2e

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_SCRIPT = PROJECT_ROOT / "nowcast_service" / "static" / "local-live.js"
LOCAL_STYLE = PROJECT_ROOT / "nowcast_service" / "static" / "local-live.css"
PUBLIC_SCRIPT = PROJECT_ROOT / "public-live-banner.js"


def make_snapshot(state: str, snapshot_id: str) -> dict[str, object]:
    hazardous = state in {"RED", "YELLOW"}
    return {
        "snapshotId": snapshot_id,
        "evaluationAt": "2026-08-05T09:00:00Z",
        "equipmentState": "DEPLOYED",
        "earliestPossibleClearAt": "2026-08-05T09:30:00Z" if hazardous else None,
        "hardwareRisk": {
            "state": state,
            "dataQuality": "COMPLETE",
            "action": "COVER_EQUIPMENT_NOW" if state == "RED" else "MAY_OPERATE",
            "reasonCodes": ["CAP_RELEVANT_WARNING_RED"] if state == "RED" else [],
            "reasons": ["Amtliche Gewitterwarnung am Standort."] if state == "RED" else [],
        },
        "activeHazards": (
            [
                {
                    "hazardKey": "DWD_CAP:CAP_RELEVANT_WARNING_RED",
                    "source": "DWD_CAP",
                    "state": "RED",
                    "reasonCode": "CAP_RELEVANT_WARNING_RED",
                    "reason": "Amtliche Gewitterwarnung am Standort.",
                    "holdUntil": "2026-08-05T09:30:00Z",
                }
            ]
            if state == "RED"
            else []
        ),
        "sources": [
            {
                "sourceId": "DWD_RV",
                "state": "LIVE",
                "ageSeconds": 45,
                "isComplete": True,
                "payload": {
                    "nearestPrecipitationDistanceKm": 18.5,
                    "nearestPrecipitationDirection": "WEST",
                    "arrivalMinutes": 35,
                    "siteIntensity": 0.0,
                    "peakIntensity": 1.4,
                    "intensityUnit": "mm/5min",
                    "coverage0To120": True,
                },
            },
            {
                "sourceId": "DWD_CAP",
                "state": "LIVE",
                "ageSeconds": 60,
                "isComplete": True,
                "payload": {
                    "active": (
                        [
                            {
                                "event": "GEWITTER",
                                "headline": "Amtliche Warnung vor Gewitter",
                                "severity": "Moderate",
                                "expires": "2026-08-05T10:00:00Z",
                            }
                        ]
                        if state == "RED"
                        else []
                    )
                },
            },
            {
                "sourceId": "LOCAL_PERSISTENCE",
                "state": "LIVE",
                "ageSeconds": 0,
                "isComplete": True,
                "payload": {},
            },
        ],
    }


def install_browser_doubles(page: Page) -> None:
    page.add_init_script(
        """
        window.__permissionRequests = 0;
        window.__notifications = [];
        window.__toneStarts = 0;

        class FakeNotification {
          static permission = "default";
          static async requestPermission() {
            window.__permissionRequests += 1;
            FakeNotification.permission = "granted";
            return "granted";
          }
          constructor(title, options) {
            window.__notifications.push({title, options});
          }
        }
        Object.defineProperty(window, "Notification", {value: FakeNotification, configurable: true});

        class FakeParam {
          setValueAtTime() {}
          exponentialRampToValueAtTime() {}
        }
        class FakeOscillator {
          constructor() { this.frequency = new FakeParam(); this.type = "sine"; }
          connect() {}
          start() { window.__toneStarts += 1; }
          stop() {}
        }
        class FakeGain {
          constructor() { this.gain = new FakeParam(); }
          connect() {}
        }
        class FakeAudioContext {
          constructor() { this.state = "suspended"; this.currentTime = 0; this.destination = {}; }
          createOscillator() { return new FakeOscillator(); }
          createGain() { return new FakeGain(); }
          async resume() { this.state = "running"; }
        }
        Object.defineProperty(window, "AudioContext", {value: FakeAudioContext, configurable: true});

        class FakeEventSource {
          constructor(url) {
            this.url = url;
            this.listeners = {};
            window.__awcEventSource = this;
            setTimeout(() => { if (this.onopen) this.onopen(); }, 0);
          }
          addEventListener(type, callback) {
            (this.listeners[type] ||= []).push(callback);
          }
          emit(type, payload) {
            const event = {data: JSON.stringify({payload})};
            for (const callback of this.listeners[type] || []) callback(event);
          }
          fail() { if (this.onerror) this.onerror(new Event("error")); }
          close() {}
        }
        Object.defineProperty(window, "EventSource", {value: FakeEventSource, configurable: true});
        """
    )


def test_live_panel_alarm_accessibility_sse_and_connection_loss() -> None:
    api_state = {
        "snapshot": make_snapshot("GREEN", "snapshot-green"),
        "alerts": {"active": None},
    }
    posts: list[str] = []

    def route_request(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path
        if request.method == "POST":
            posts.append(path)
            route.fulfill(status=200, content_type="application/json", body="{}")
        elif path == "/":
            route.fulfill(
                status=200,
                content_type="text/html",
                body=(
                    "<!doctype html><html><head><title>Forecast</title>"
                    '<link rel="stylesheet" href="/local-live.css"></head>'
                    '<body><main id="forecast-existing">Forecast bleibt erhalten</main>'
                    '<script src="/local-live.js" defer></script></body></html>'
                ),
            )
        elif path == "/local-live.js":
            route.fulfill(
                status=200,
                content_type="text/javascript",
                body=LOCAL_SCRIPT.read_text(encoding="utf-8"),
            )
        elif path == "/local-live.css":
            route.fulfill(
                status=200,
                content_type="text/css",
                body=LOCAL_STYLE.read_text(encoding="utf-8"),
            )
        elif path == "/runtime-config.json":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "mode": "LOCAL",
                        "localApiAvailable": True,
                        "browserAudioEnabled": True,
                        "browserNotificationsEnabled": True,
                    }
                ),
            )
        elif path == "/api/v1/safety":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(api_state["snapshot"]),
            )
        elif path == "/api/v1/alerts":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(api_state["alerts"]),
            )
        elif path == "/api/v1/security/csrf":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"token": "test-token"}),
            )
        else:
            route.fulfill(status=404, body="not found")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        install_browser_doubles(page)
        page.route("**/*", route_request)
        page.goto("http://awc.test/")

        panel = page.locator("#awc-live-panel")
        panel.wait_for()
        assert panel.get_attribute("data-state") == "GREEN"
        assert page.locator("#awc-state").inner_text().startswith("GRÜN")
        assert page.locator("#awc-state-symbol").inner_text() == "✓"
        assert page.locator("#awc-snapshot").inner_text() == "snapshot-green"
        assert page.locator("#forecast-existing").inner_text() == "Forecast bleibt erhalten"

        page.keyboard.press("Tab")
        assert page.locator(":focus").get_attribute("id") == "awc-enable-alerts"
        page.keyboard.press("Enter")
        page.wait_for_function("window.__permissionRequests === 1")
        assert "Akustische Alarme aktiviert" in page.locator("#awc-alarm-capability").inner_text()

        red = make_snapshot("RED", "snapshot-red")
        api_state["snapshot"] = red
        page.evaluate("payload => window.__awcEventSource.emit('snapshot', payload)", red)
        page.evaluate(
            """payload => window.__awcEventSource.emit('alert', payload)""",
            {
                "eventId": "alert-red-1",
                "snapshotId": "snapshot-red",
                "eventType": "RED",
                "riskState": "RED",
                "reasonCodes": ["CAP_RELEVANT_WARNING_RED"],
                "requiresAttention": True,
                "acknowledgedAt": None,
            },
        )
        page.wait_for_function("window.__toneStarts === 3")
        page.wait_for_function("window.__notifications.length === 1")
        assert panel.get_attribute("data-state") == "RED"
        assert page.locator("#awc-state-symbol").inner_text() == "⛔"
        assert "Hardware schützen" in page.locator("#awc-state").inner_text()
        assert "Amtliche Warnung vor Gewitter" in page.locator("#awc-warnings").inner_text()
        assert "CAP_RELEVANT_WARNING_RED" in page.locator("#awc-hazards").inner_text()
        assert page.title().startswith("ROT –")
        assert page.locator("#awc-ack").is_enabled()

        page.locator("#awc-ack").focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(100)
        assert "/api/v1/alerts/acknowledge" in posts

        page.evaluate("window.dispatchEvent(new Event('offline'))")
        assert panel.get_attribute("data-state") == "RED"
        assert "offline" in page.locator("#awc-connection").inner_text().lower()

        green = make_snapshot("GREEN", "snapshot-green-2")
        api_state["snapshot"] = green
        page.evaluate("payload => window.__awcEventSource.emit('snapshot', payload)", green)
        assert panel.get_attribute("data-state") == "GREEN"
        page.evaluate("window.dispatchEvent(new Event('offline'))")
        assert panel.get_attribute("data-state") == "UNKNOWN"
        assert page.locator("#awc-state-symbol").inner_text() == "?"
        assert page.locator("#awc-action").inner_text() == "UNKNOWN_DO_NOT_RELY"
        assert page.title().startswith("UNBEKANNT –")

        browser.close()


def test_public_banner_never_renders_local_green() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content("<!doctype html><html><body><main>Öffentliche Forecast-Seite</main></body></html>")
        page.add_script_tag(content=PUBLIC_SCRIPT.read_text(encoding="utf-8"))
        banner = page.locator("section[role='status']")
        assert "LIVE-ÜBERWACHUNG NICHT VERBUNDEN" in banner.inner_text()
        assert page.locator("[data-state='GREEN']").count() == 0
        browser.close()
