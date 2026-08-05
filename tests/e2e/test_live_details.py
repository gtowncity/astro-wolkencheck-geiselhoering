import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, Route, sync_playwright

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "nowcast_service" / "static"


def snapshot(*, red: bool = False) -> dict[str, object]:
    warning = {
        "identifier": "storm" if red else "heat",
        "event": "SCHWERES GEWITTER" if red else "HITZE",
        "headline": "Amtliche Warnung vor schwerem Gewitter" if red else "Warnung vor Hitze",
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
        "activeHazards": ([{
            "hazardKey": "DWD_CAP:CAP_RELEVANT_WARNING_RED",
            "source": "DWD_CAP",
            "reason": "Schweres Gewitter bleibt vorsorglich aktiv.",
            "lastConfirmedAt": "2026-08-05T09:00:00Z",
            "holdUntil": "2026-08-05T09:30:00Z",
            "clearStreak": 1,
            "clearCyclesRequired": 2,
            "clearCondition": "Zwei frische vollständige Warnungszyklen ohne Gewitter.",
        }] if red else []),
        "sourceStates": {"DWD_RV": "LIVE", "DWD_CAP": "LIVE", "LOCAL_PERSISTENCE": "LIVE"},
        "sources": [
            {"sourceId": "DWD_RV", "state": "LIVE", "ageSeconds": 60, "isComplete": True, "staleAfterSeconds": 900, "invalidAfterSeconds": 1800, "payload": {"frameCount": 25, "forecastHorizonMinutes": 120, "rainNow": False, "movingTowardSite": False, "siteIntensityMm5Min": 0.0, "peakIntensityMm5Min": 0.0, "unit": "mm/5min", "coverage0To120": True}},
            {"sourceId": "DWD_CAP", "state": "LIVE", "ageSeconds": 60, "isComplete": True, "staleAfterSeconds": 1200, "invalidAfterSeconds": 2700, "payload": {"active": [warning]}},
            {"sourceId": "LOCAL_PERSISTENCE", "state": "LIVE", "ageSeconds": 0, "isComplete": True, "staleAfterSeconds": 86400, "invalidAfterSeconds": 172800, "payload": {}},
        ],
    }


def shell() -> str:
    return """<!doctype html><html><head><meta charset='utf-8'><title>Forecast</title>
    <link rel='stylesheet' href='/local-live.css'><link rel='stylesheet' href='/local-live-details.css'></head>
    <body><main class='app'><header class='app-header'><div class='brand-block'><h1>Astro-Wolkencheck</h1></div></header>
    <nav class='tabs-shell'><button class='tab-button' data-tab='overview'>Übersicht</button><select id='mobileTabSelect'><option value='overview'>Übersicht</option></select></nav>
    <section id='overviewContent'><article class='decision-hero good'><h2>Gute Chance</h2></article></section></main>
    <script src='/local-live.js' defer></script><script src='/local-live-details.js' defer></script></body></html>"""


def install_doubles(page: Page) -> None:
    page.add_init_script("""
    window.__notifications=[];
    class N {static permission='granted';static async requestPermission(){return 'granted'}constructor(title,options){window.__notifications.push({title,options})}}
    Object.defineProperty(window,'Notification',{value:N,configurable:true});
    class E {constructor(){this.listeners={};window.__events=this;setTimeout(()=>this.emit('open',{}),0)}addEventListener(t,c){(this.listeners[t]||=[]).push(c)}emit(t,p){const e=['open','error'].includes(t)?p:{data:JSON.stringify({payload:p})};for(const c of this.listeners[t]||[])c(e)}close(){}}
    Object.defineProperty(window,'EventSource',{value:E,configurable:true});
    """)


def test_warning_latch_and_alarm_details_are_structured() -> None:
    current = {"snapshot": snapshot()}

    def route_request(route: Route) -> None:
        path = urlparse(route.request.url).path
        assets = {
            "/local-live.js": ("text/javascript", STATIC / "local-live.js"),
            "/local-live.css": ("text/css", STATIC / "local-live.css"),
            "/local-live-details.js": ("text/javascript", STATIC / "local-live-details.js"),
            "/local-live-details.css": ("text/css", STATIC / "local-live-details.css"),
        }
        if path == "/":
            route.fulfill(status=200, content_type="text/html", body=shell())
        elif path in assets:
            content_type, file_path = assets[path]
            route.fulfill(status=200, content_type=content_type, body=file_path.read_text(encoding="utf-8"))
        elif path == "/runtime-config.json":
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"browserAudioEnabled": False, "browserNotificationsEnabled": True}))
        elif path == "/api/v1/safety":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(current["snapshot"]))
        elif path == "/api/v1/alerts":
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"active": None}))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"token": "x"}))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        install_doubles(page)
        page.route("**/*", route_request)
        page.goto("http://awc.test/")
        page.locator(".awc-warning-facts").wait_for()

        warning_text = page.locator(".awc-warning-facts").inner_text()
        assert "Dringlichkeit" in warning_text and "erwartet" in warning_text
        assert "Sicherheit" in warning_text and "wahrscheinlich" in warning_text
        assert "Deutscher Wetterdienst" in warning_text
        assert page.locator("#awc-alarm-capabilities").is_visible()
        assert page.locator("#awc-test-notification").is_visible()
        page.locator("#awc-test-notification").click()
        page.wait_for_function("window.__notifications.length === 1")

        red = snapshot(red=True)
        page.evaluate("payload=>window.__events.emit('snapshot',payload)", red)
        page.locator(".awc-hazard-entry").wait_for()
        hazard_text = page.locator(".awc-hazard-entry").inner_text()
        assert "1 von 2 frischen Zyklen" in hazard_text
        assert "Mindesthaltezeit" in hazard_text
        assert "Quittiert" in hazard_text
        assert "niemals die erkannte Gefahr" in page.locator("#awc-ack-note").inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        browser.close()
