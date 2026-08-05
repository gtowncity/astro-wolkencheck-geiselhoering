import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, Route, sync_playwright

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "nowcast_service" / "static"
PUBLIC_SCRIPT = ROOT / "public-live-banner.js"


def timeline(approaching: bool = False) -> list[dict[str, object]]:
    return [
        {
            "leadMinutes": lead,
            "coverageSufficient": True,
            "validFractionSite": 1.0,
            "rainAtSite": approaching and lead >= 35,
            "siteWetPixelCount": 4 if approaching and lead >= 35 else 0,
            "siteIntensityMm5Min": 0.4 if approaching and lead >= 35 else 0.0,
            "nearestDistanceKm": max(0.0, 83 - index * 1.3 if approaching else 83 + index / 3),
            "nearestBearingDeg": 90.0,
            "nearestDirection": "E",
            "componentAreaKm2": 121.0,
            "componentMaximumMm5Min": 0.8,
            "rings": [{"radiusKm": 10.0, "validFraction": 1.0, "wetPixelCount": 0}],
        }
        for index, lead in enumerate(range(0, 121, 5))
    ]


def warnings(red: bool) -> list[dict[str, object]]:
    if red:
        return [{
            "identifier": "storm",
            "event": "SCHWERES GEWITTER",
            "headline": "Amtliche Warnung vor schwerem Gewitter",
            "severity": "Severe",
            "expires": "2026-08-05T10:00:00Z",
            "description": "Schweres Gewitter am Standort möglich.",
            "instruction": "Ausrüstung sofort schützen.",
        }]
    return [
        {
            "identifier": key,
            "event": event,
            "headline": headline,
            "severity": severity,
            "expires": "2026-08-05T17:00:00Z",
            "description": "Wärmebelastung.",
            "instruction": "Direkte Sonne meiden.",
        }
        for key, event, headline, severity in (
            ("heat-extreme", "EXTREME HITZE", "Warnung vor extremer Hitze", "Severe"),
            ("heat", "HITZE", "Warnung vor Hitze", "Moderate"),
        )
    ]


def snapshot(state: str, snapshot_id: str) -> dict[str, object]:
    red = state == "RED"
    return {
        "snapshotId": snapshot_id,
        "evaluationAt": "2026-08-05T09:00:00Z",
        "algorithmVersion": "safety-1.0.0",
        "configurationVersion": "1",
        "equipmentState": "DEPLOYED_ATTENDED",
        "hardwareRisk": {
            "state": state,
            "dataQuality": "COMPLETE",
            "action": "COVER_EQUIPMENT_NOW" if red else "NO_LIVE_VETO_DETECTED",
            "reasonCodes": ["CAP_RELEVANT_WARNING_RED"] if red else [],
            "reasons": ["Amtliche Gewitterwarnung am Standort."] if red else [],
        },
        "activeHazards": ([{
            "hazardKey": "DWD_CAP:CAP_RELEVANT_WARNING_RED",
            "source": "DWD_CAP",
            "state": "RED",
            "reasonCode": "CAP_RELEVANT_WARNING_RED",
            "reason": "Amtliche Gewitterwarnung am Standort.",
            "lastConfirmedAt": "2026-08-05T09:00:00Z",
            "holdUntil": "2026-08-05T09:30:00Z",
            "clearStreak": 0,
            "clearCyclesRequired": 2,
        }] if red else []),
        "sourceStates": {"DWD_RV": "LIVE", "DWD_CAP": "LIVE", "LOCAL_PERSISTENCE": "LIVE"},
        "sources": [
            {
                "sourceId": "DWD_RV",
                "state": "LIVE",
                "ageSeconds": 45,
                "isComplete": True,
                "cycleTime": "2026-08-05T08:55:00Z",
                "validUntil": "2026-08-05T09:25:00Z",
                "staleAfterSeconds": 900,
                "invalidAfterSeconds": 1800,
                "payload": {
                    "cycleTime": "2026-08-05T08:55:00Z",
                    "frameCount": 25,
                    "forecastHorizonMinutes": 120,
                    "nearestPrecipitationDistanceKm": 18.5 if red else 83.0,
                    "nearestPrecipitationDirection": "E",
                    "arrivalMinutes": 35 if red else None,
                    "arrivalWindow": ({"earliestMinutes": 30, "latestMinutes": 40, "confidence": "HIGH"} if red else None),
                    "rainNow": False,
                    "movingTowardSite": red,
                    "siteIntensityMm5Min": 0.0,
                    "peakIntensityMm5Min": 1.4 if red else 0.0,
                    "peakIntensityLeadMinutes": 35 if red else 0,
                    "affectedAreaKm2": 121.0,
                    "unit": "mm/5min",
                    "coverage0To60": True,
                    "coverage0To120": True,
                    "frameTimeline": timeline(red),
                },
            },
            {
                "sourceId": "DWD_CAP",
                "state": "LIVE",
                "ageSeconds": 60,
                "isComplete": True,
                "validUntil": "2026-08-05T09:45:00Z",
                "staleAfterSeconds": 1200,
                "invalidAfterSeconds": 2700,
                "payload": {"active": warnings(red)},
            },
            {"sourceId": "LOCAL_PERSISTENCE", "state": "LIVE", "ageSeconds": 0, "isComplete": True, "staleAfterSeconds": 86400, "invalidAfterSeconds": 172800, "payload": {}},
            {"sourceId": "DWD_WN", "state": "NOT_AVAILABLE", "ageSeconds": 0, "isComplete": False, "staleAfterSeconds": 900, "invalidAfterSeconds": 1800, "payload": {}},
            {"sourceId": "RAIN_SENSOR", "state": "DISABLED", "ageSeconds": 0, "isComplete": False, "staleAfterSeconds": 60, "invalidAfterSeconds": 120, "payload": {}},
        ],
    }


def shell() -> str:
    return """<!doctype html><html lang='de'><head><meta charset='utf-8'><title>Forecast</title>
    <link rel='stylesheet' href='/local-live.css'><link rel='stylesheet' href='/local-live-timeline.css'></head>
    <body><main class='app'><header class='app-header'><div class='brand-block'><h1>Astro Wolkencheck</h1>
    <p class='location-line'>Geiselhöring</p></div><p class='update-line'><strong id='updatedMetric'>05.08.2026, 11:00</strong></p></header>
    <nav class='tabs-shell'><button class='tab-button' data-tab='overview'>Übersicht</button>
    <button class='tab-button' data-tab='windows'>Beste Zeiten</button><button class='tab-button' data-tab='hours'>Stunden</button>
    <button class='tab-button' data-tab='data'>Daten</button><select id='mobileTabSelect'><option value='overview'>Übersicht</option></select></nav>
    <section id='overviewContent'><article class='decision-hero warn'><h2>Nur unter Vorbehalt</h2><div class='decision-facts'>
    <div><span>Größtes Risiko</span><strong>Forecast nur mittel sicher</strong></div>
    <div><span>Sicherheit</span><strong>mittel · 62/100</strong></div></div></article></section></main>
    <script src='/local-live.js' defer></script><script src='/local-live-timeline.js' defer></script></body></html>"""


def install_doubles(page: Page) -> None:
    page.add_init_script("""
    window.__notifications=[]; window.__toneStarts=0;
    class N { static permission='granted'; static async requestPermission(){return 'granted'} constructor(title,options){window.__notifications.push({title,options})} }
    Object.defineProperty(window,'Notification',{value:N,configurable:true});
    class P {setValueAtTime(){} exponentialRampToValueAtTime(){}}
    class O {constructor(){this.frequency=new P()} connect(){} start(){window.__toneStarts++} stop(){}}
    class G {constructor(){this.gain=new P()} connect(){}}
    class A {constructor(){this.state='running';this.currentTime=0;this.destination={}} createOscillator(){return new O()} createGain(){return new G()} async resume(){}}
    Object.defineProperty(window,'AudioContext',{value:A,configurable:true});
    class E {constructor(){this.listeners={};window.__events=this;setTimeout(()=>this.emit('open',{}),0)} addEventListener(t,c){(this.listeners[t]||=[]).push(c)} emit(t,p){const e=['open','error'].includes(t)?p:{data:JSON.stringify({payload:p})};for(const c of this.listeners[t]||[])c(e)} close(){} }
    Object.defineProperty(window,'EventSource',{value:E,configurable:true});
    """)


def test_redesign_dashboard_safety_timeline_and_mobile_layout() -> None:
    current = {"snapshot": snapshot("GREEN", "green")}
    writes: list[tuple[str, str]] = []

    def route_request(route: Route) -> None:
        request = route.request
        path = urlparse(request.url).path
        if request.method in {"POST", "PATCH"}:
            writes.append((request.method, path))
            route.fulfill(status=200, content_type="application/json", body="{}")
            return
        assets = {
            "/local-live.js": ("text/javascript", STATIC / "local-live.js"),
            "/local-live.css": ("text/css", STATIC / "local-live.css"),
            "/local-live-timeline.js": ("text/javascript", STATIC / "local-live-timeline.js"),
            "/local-live-timeline.css": ("text/css", STATIC / "local-live-timeline.css"),
        }
        if path == "/":
            route.fulfill(status=200, content_type="text/html", body=shell())
        elif path in assets:
            content_type, file_path = assets[path]
            route.fulfill(status=200, content_type=content_type, body=file_path.read_text(encoding="utf-8"))
        elif path == "/runtime-config.json":
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"browserAudioEnabled": True, "browserNotificationsEnabled": True}))
        elif path == "/api/v1/safety":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(current["snapshot"]))
        elif path == "/api/v1/alerts":
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"active": None}))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"token": "x"}))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1165, "height": 768})
        install_doubles(page)
        page.route("**/*", route_request)
        page.goto("http://awc.test/")
        page.locator("#awc-timeline-path").wait_for(state="attached")

        assert page.locator("#awc-live-axis").get_attribute("data-tone") == "GREEN"
        assert page.locator("#awc-forecast-title").inner_text() == "Nur unter Vorbehalt"
        assert page.locator("#awc-data-title").inner_text() == "Live-Daten vollständig"
        assert page.locator("#awc-warning-list").inner_text().count("Keine direkte Hardware-Sperre") == 2
        assert "Fläche entfernt sich" in page.locator("#awc-timeline-summary").inner_text()
        assert "25 validierte Radarframes" in page.locator("#awc-timeline-detail").inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")

        page.locator("#awc-set-equipment").click()
        page.locator(".awc-equipment-option").first.click()
        assert ("PATCH", "/api/v1/session") in writes
        page.locator("#awc-enable-alerts").click()
        page.locator("#awc-test-alarm").click()
        page.wait_for_function("window.__toneStarts >= 2")
        page.evaluate("window.__notifications=[]")

        red = snapshot("RED", "red")
        page.evaluate("payload=>window.__events.emit('snapshot',payload)", red)
        page.evaluate("payload=>window.__events.emit('alert',payload)", {"eventId":"a1","snapshotId":"red","riskState":"RED","requiresAttention":True,"reasonCodes":["CAP_RELEVANT_WARNING_RED"]})
        page.wait_for_function("window.__notifications.length === 1")
        assert page.locator("#awc-live-axis").get_attribute("data-tone") == "RED"
        assert "Hardware-Relevanz: ROT" in page.locator("#awc-warning-list").inner_text()
        assert page.locator("#awc-hazard-card").is_visible()
        page.locator("#awc-ack").click()
        assert ("POST", "/api/v1/alerts/acknowledge") in writes

        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        assert page.locator("#awc-radar-metrics").is_visible()
        page.evaluate("window.__events.emit('error',new Event('error'))")
        assert page.locator("#awc-live-axis").get_attribute("data-tone") == "UNKNOWN"
        assert page.locator("#awc-api-pill").get_attribute("data-connection") == "DISCONNECTED"
        browser.close()


def test_public_banner_never_renders_local_green() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content("<!doctype html><html><body><main>Öffentliche Forecast-Seite</main></body></html>")
        page.add_script_tag(content=PUBLIC_SCRIPT.read_text(encoding="utf-8"))
        assert "LIVE-ÜBERWACHUNG NICHT VERBUNDEN" in page.locator("section[role='status']").inner_text()
        assert page.locator("[data-state='GREEN']").count() == 0
        browser.close()
