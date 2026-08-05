import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image, ImageDraw
from playwright.sync_api import Page, Route, sync_playwright

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "nowcast_service" / "static"
ARTIFACTS = ROOT / "test-artifacts"


def satellite_png() -> bytes:
    image = Image.new("RGB", (1200, 850), (24, 45, 66))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (690, 310, 725, 345),
        fill=(220, 24, 45),
        outline="white",
        width=4,
    )
    draw.text((20, 20), "EUMETSAT MTG-FCI TEST FRAME", fill="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def satellite_frames() -> list[str]:
    latest = datetime.now(UTC).replace(second=0, microsecond=0)
    return [
        (latest - timedelta(minutes=20 - index * 10))
        .isoformat()
        .replace("+00:00", "Z")
        for index in range(3)
    ]


def satellite_metadata(product: str) -> dict[str, object]:
    products = [
        {
            "key": "geocolour",
            "title": "GeoColour Tag/Nacht",
            "description": "Naturnahe Darstellung",
            "available": True,
            "fresh": True,
        },
        {
            "key": "infrared",
            "title": "Infrarot 10,5 um",
            "description": "Wolkenstruktur Tag und Nacht",
            "available": True,
            "fresh": True,
        },
        {
            "key": "cloudtype",
            "title": "Wolkentypen RGB",
            "description": "Wolkentypen",
            "available": False,
            "fresh": False,
        },
    ]
    selected = next(item for item in products if item["key"] == product)
    return {
        "provider": "EUMETSAT",
        "satellite": "Meteosat-12 / MTG-I1 / FCI",
        "selectedProduct": selected,
        "products": products,
        "frames": satellite_frames(),
        "freshnessLimitMinutes": 20,
        "location": {
            "name": "Geiselhoering",
            "latitude": 48.84,
            "longitude": 12.40,
        },
    }


def html_shell() -> str:
    return """<!doctype html>
<html lang='de'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width,initial-scale=1'>
  <style>
    :root {
      --awc-bg:#080d13; --awc-surface:#0f1721; --awc-border:#2b3a4b;
      --awc-text:#edf3f8; --awc-muted:#a7b5c3; --awc-info:#69add9;
      --awc-green:#55c38a; --awc-yellow:#dfb44f; --awc-red:#e16a76;
      --awc-radius:10px; --awc-gap:10px;
    }
    * { box-sizing:border-box; }
    body { margin:0; background:#080d13; color:#edf3f8; font-family:Arial,sans-serif; }
    .app { width:min(1480px,calc(100% - 28px)); margin:0 auto; padding:14px 0 40px; }
    .app-header,.awc-card { border:1px solid #2b3a4b; border-radius:10px; background:#0f1721; }
    .app-header { padding:14px; }
    .controls { display:grid; grid-template-columns:1fr 1fr auto; gap:9px; align-items:end; }
    .field { display:grid; gap:4px; }
    input,select,button { min-height:38px; border:1px solid #2b3a4b; border-radius:7px;
      background:#111d2a; color:#edf3f8; padding:7px 9px; }
    .request-progress { margin-top:8px; padding:8px; border:1px solid #385c73; }
    .request-progress[hidden] { display:none; }
    #overviewContent,#live-dashboard-root { margin-top:10px; }
    .decision-hero { display:grid; grid-template-columns:1fr 360px; gap:16px; padding:16px;
      border:1px solid #2b3a4b; border-left:5px solid #69add9; border-radius:10px; }
    .decision-facts { display:grid; grid-template-columns:repeat(3,1fr); }
    .decision-facts>div { padding:8px; border-left:1px solid #2b3a4b; }
    .decision-facts span,.decision-facts strong { display:block; }
    .awc-live-grid { display:grid; grid-template-columns:minmax(0,2fr) minmax(310px,.9fr);
      gap:10px; }
    .awc-side-stack,.awc-secondary-grid { display:grid; gap:10px; align-content:start; }
    .awc-secondary-grid { grid-template-columns:1fr 1fr; margin-top:10px; }
    .awc-card { min-width:0; padding:12px; }
    .awc-radar-summary { display:grid; grid-template-columns:minmax(0,1fr) 1fr; gap:12px; }
    .awc-radar-visual { min-width:0; }
    .awc-hazard-entry { display:flex; gap:8px; }
    @media(max-width:900px) {
      .awc-live-grid,.awc-secondary-grid,.awc-radar-summary { grid-template-columns:1fr; }
    }
  </style>
</head>
<body>
  <main class='app'>
    <header class='app-header awc-unified-header'>
      <h1>Astro-Wolkencheck</h1>
      <div class='controls'>
        <label class='field'>Beginn
          <input id='startInput' type='datetime-local' value='2026-08-07T22:00'>
        </label>
        <label class='field'>Ende
          <input id='endInput' type='datetime-local' value='2026-08-09T06:00'>
        </label>
        <button id='refreshBtn' type='button'>Wetter neu laden</button>
      </div>
      <div id='requestProgress' class='request-progress' hidden>
        <strong id='progressMetric'>0 von 16 Quellen verarbeitet</strong>
      </div>
      <div id='cacheNotice'></div>
    </header>
    <section id='overviewContent'></section>
    <section id='live-dashboard-root'>
      <div class='awc-live-grid'>
        <article class='awc-card awc-radar-card'>
          <h2>Radar und Satellit</h2>
          <div class='awc-radar-summary'>
            <div class='awc-radar-visual'></div>
            <div id='awc-radar-metrics'>Radar-Messwerte</div>
          </div>
        </article>
        <div class='awc-side-stack'>
          <article class='awc-card awc-warnings-card'>Warnungen</article>
          <article class='awc-card awc-sources-card'>Quellen</article>
          <article class='awc-card awc-hazard-card'>
            <div class='awc-hazard-entry'>
              <strong>Gespeicherte Gefahr</strong><span>Text</span>
            </div>
          </article>
        </div>
      </div>
      <div class='awc-secondary-grid'>
        <article class='awc-card awc-change-card'>Aenderungen</article>
        <article class='awc-card awc-alarm-card'>Alarmierung</article>
      </div>
    </section>
  </main>
  <script>
    document.getElementById('refreshBtn').addEventListener('click', async () => {
      const button = document.getElementById('refreshBtn');
      const progress = document.getElementById('requestProgress');
      button.disabled = true;
      progress.hidden = false;
      document.getElementById('progressMetric').textContent =
        '4 von 16 Quellen verarbeitet';
      try {
        await fetch('https://api.open-meteo.com/v1/forecast?latitude=0&longitude=0');
      } finally {
        progress.hidden = true;
        button.disabled = false;
      }
    });
  </script>
</body>
</html>"""


def install_fullscreen_stub(page: Page) -> None:
    page.add_init_script(
        """
        window.__fullscreenCalls = 0;
        window.__fullscreenElement = null;
        Object.defineProperty(document, 'fullscreenElement', {
          get() { return window.__fullscreenElement; }, configurable: true,
        });
        HTMLElement.prototype.requestFullscreen = async function() {
          window.__fullscreenCalls += 1;
          window.__fullscreenElement = this;
          document.dispatchEvent(new Event('fullscreenchange'));
        };
        document.exitFullscreen = async function() {
          window.__fullscreenElement = null;
          document.dispatchEvent(new Event('fullscreenchange'));
        };
        """
    )


def add_modules(page: Page) -> None:
    page.add_style_tag(
        content=(STATIC / "local-ui-recovery.css").read_text(encoding="utf-8")
    )
    page.add_style_tag(
        content=(STATIC / "local-satellite-viewer.css").read_text(encoding="utf-8")
    )
    page.add_script_tag(
        content=(STATIC / "local-ui-recovery.js").read_text(encoding="utf-8")
    )
    page.add_script_tag(
        content=(STATIC / "local-satellite-viewer.js").read_text(encoding="utf-8")
    )


def test_deferred_forecast_compact_layout_and_satellite_controls() -> None:
    requests: list[str] = []

    def route_request(route: Route) -> None:
        parsed = urlparse(route.request.url)
        path = parsed.path
        if path == "/":
            route.fulfill(status=200, content_type="text/html", body=html_shell())
        elif path == "/api/v1/satellite/meta":
            product = parse_qs(parsed.query).get("product", ["geocolour"])[0]
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(satellite_metadata(product)),
            )
        elif path == "/api/v1/satellite/image":
            requests.append(route.request.url)
            route.fulfill(status=200, content_type="image/png", body=satellite_png())
        elif parsed.netloc == "geocoding-api.open-meteo.com":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "results": [
                            {
                                "name": "Muenchen",
                                "admin1": "Bayern",
                                "admin2": "Muenchen",
                                "latitude": 48.137,
                                "longitude": 11.575,
                            }
                        ]
                    }
                ),
            )
        elif parsed.netloc in {
            "api.open-meteo.com",
            "ensemble-api.open-meteo.com",
        }:
            requests.append(route.request.url)
            route.fulfill(status=200, content_type="application/json", body="{}")
        else:
            route.fulfill(status=404, body="not found")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        install_fullscreen_stub(page)
        page.route("**/*", route_request)
        page.goto("http://awc.test/")
        add_modules(page)
        page.locator("#awc-satellite-image").wait_for(state="attached")
        page.wait_for_function(
            "document.getElementById('awc-satellite-image').complete && "
            "document.getElementById('awc-satellite-image').naturalWidth > 0"
        )

        assert [url for url in requests if "open-meteo.com" in url] == []
        assert page.locator("#awc-forecast-placeholder-title").inner_text() == (
            "Forecast noch nicht gestartet"
        )
        assert page.locator("#awc-location-control").is_visible()
        assert page.locator(".awc-main-stack").count() == 1
        assert page.locator(".awc-secondary-grid").count() == 0
        assert page.locator(".awc-hazard-entry").evaluate(
            "node => getComputedStyle(node).display"
        ) == "block"

        with page.expect_request(
            lambda request: "api.open-meteo.com" in request.url
        ):
            page.locator("#refreshBtn").click()
        forecast_url = next(url for url in requests if "api.open-meteo.com" in url)
        query = parse_qs(urlparse(forecast_url).query)
        assert query["latitude"] == ["48.84"]
        assert query["longitude"] == ["12.4"]

        page.locator("#awc-location-query").fill("Muenchen")
        page.locator("#awc-location-search").click()
        page.locator("#awc-location-results").wait_for(state="visible")
        page.locator("#awc-location-results").select_option("0")
        assert "Live-Sicherheit bleibt Geiselhöring" in page.locator(
            "#awc-location-status"
        ).inner_text()

        requests.clear()
        with page.expect_request(
            lambda request: "api.open-meteo.com" in request.url
        ):
            page.locator("#refreshBtn").click()
        forecast_url = next(url for url in requests if "api.open-meteo.com" in url)
        query = parse_qs(urlparse(forecast_url).query)
        assert query["latitude"] == ["48.137"]
        assert query["longitude"] == ["11.575"]

        page.locator("#awc-satellite-product").select_option("infrared")
        page.wait_for_function(
            "window.AstroWolkencheckSatelliteViewer.getState().product === 'infrared'"
        )
        assert page.locator("#awc-satellite-range").get_attribute("max") == "2"

        page.locator("#awc-satellite-zoom-in").click()
        assert "scale(1.35)" in page.locator("#awc-satellite-image").evaluate(
            "node => node.style.transform"
        )

        page.locator("#awc-satellite-fullscreen").click()
        page.wait_for_function("window.__fullscreenCalls === 1")
        assert page.locator("#awc-satellite-fullscreen").inner_text() == (
            "Vollbild schließen"
        )
        page.locator("#awc-satellite-fullscreen").click()

        page.locator("#awc-satellite-range").evaluate(
            """node => {
              node.value = '0';
              node.dispatchEvent(new Event('input', {bubbles: true}));
            }"""
        )
        page.locator("#awc-satellite-play").click()
        page.wait_for_function(
            "Number(document.getElementById('awc-satellite-range').value) > 0"
        )
        page.locator("#awc-satellite-play").click()

        page.locator(".awc-radar-visual").evaluate("node => node.replaceChildren()")
        page.locator("#awc-satellite-viewer").wait_for(state="attached")

        ARTIFACTS.mkdir(exist_ok=True)
        page.screenshot(
            path=str(ARTIFACTS / "forecast-satellite-desktop.png"),
            full_page=True,
        )
        no_overflow = (
            "document.documentElement.scrollWidth <= "
            "document.documentElement.clientWidth"
        )
        assert page.evaluate(no_overflow)

        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate(no_overflow)
        page.screenshot(
            path=str(ARTIFACTS / "forecast-satellite-mobile.png"),
            full_page=True,
        )
        browser.close()
