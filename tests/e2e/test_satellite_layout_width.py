from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "nowcast_service" / "static"


def shell() -> str:
    return """<!doctype html>
<html lang='de'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width,initial-scale=1'>
  <style>
    :root {
      --awc-bg:#080d13; --awc-surface:#0f1721; --awc-border:#2b3a4b;
      --awc-text:#edf3f8; --awc-muted:#a7b5c3; --awc-info:#69add9;
      --awc-radius:10px; --awc-gap:10px;
    }
    * { box-sizing:border-box; }
    body { margin:0; background:#080d13; color:#edf3f8; }
    .app { width:min(1180px,calc(100% - 24px)); margin:12px auto; }
    .awc-radar-card { padding:12px; border:1px solid #2b3a4b; border-radius:10px; }
    .awc-radar-summary {
      display:grid;
      grid-template-columns:minmax(0,1fr) minmax(0,1fr);
      gap:12px;
    }
    #awc-radar-metrics { min-height:90px; border:1px solid #2b3a4b; }
  </style>
</head>
<body>
  <main class='app'>
    <article class='awc-radar-card'>
      <div class='awc-radar-summary'>
        <div class='awc-radar-visual awc-satellite-host'>
          <section class='awc-satellite-viewer'>
            <div class='awc-satellite-viewport'></div>
          </section>
        </div>
        <div id='awc-radar-metrics'>Radarwerte</div>
      </div>
    </article>
  </main>
</body>
</html>"""


def test_satellite_viewer_uses_full_card_width_and_metrics_follow_below() -> None:
    recovery = (STATIC / "local-ui-recovery.css").read_text(encoding="utf-8")
    satellite = (STATIC / "local-satellite-viewer.css").read_text(encoding="utf-8")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_content(shell())
        page.add_style_tag(content=recovery)
        page.add_style_tag(content=satellite)

        card = page.locator(".awc-radar-card").bounding_box()
        viewer = page.locator(".awc-satellite-host").bounding_box()
        metrics = page.locator("#awc-radar-metrics").bounding_box()
        assert card is not None and viewer is not None and metrics is not None
        assert viewer["width"] >= card["width"] * 0.94
        assert metrics["y"] >= viewer["y"] + viewer["height"]

        no_overflow = (
            "document.documentElement.scrollWidth <= "
            "document.documentElement.clientWidth"
        )
        assert page.evaluate(no_overflow)

        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate(no_overflow)
        mobile_viewer = page.locator(".awc-satellite-host").bounding_box()
        mobile_card = page.locator(".awc-radar-card").bounding_box()
        assert mobile_viewer is not None and mobile_card is not None
        assert mobile_viewer["width"] >= mobile_card["width"] * 0.92
        browser.close()
