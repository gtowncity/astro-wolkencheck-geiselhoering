(() => {
  "use strict";

  const banner = document.getElementById("preview-banner");
  const controls = document.getElementById("preview-controls");

  function ensureForecastShell() {
    if (document.querySelector(".app")) return;

    const app = document.createElement("main");
    app.id = "preview-app";
    app.className = "app";
    app.innerHTML = `
      <header class="app-header">
        <div class="brand-block">
          <h1>Astro-Wolkencheck · Vorschau</h1>
          <p class="location-line">Simulierte lokale Sicherheitslage</p>
        </div>
      </header>
      <div class="tabs-shell" aria-hidden="true">
        <nav class="tabs">
          <button class="tab-button" data-tab="overview" type="button">Übersicht</button>
          <button class="tab-button" data-tab="windows" type="button">Beste Zeiten</button>
          <button class="tab-button" data-tab="hours" type="button">Stunden</button>
          <button class="tab-button" data-tab="data" type="button">Daten</button>
        </nav>
        <select id="mobileTabSelect" tabindex="-1">
          <option value="overview">Übersicht</option>
          <option value="windows">Beste Zeiten</option>
          <option value="hours">Stunden</option>
          <option value="data">Daten</option>
        </select>
      </div>
      <section id="overviewContent" hidden>
        <article class="decision-hero warn">
          <h2>Vorschau-Forecast</h2>
          <div class="decision-facts">
            <div><span>Hinweis</span><strong>Nur simulierte Daten</strong></div>
          </div>
        </article>
      </section>
    `;

    const anchor = controls || document.getElementById("forecast-preview");
    document.body.insertBefore(app, anchor);
  }

  function loadDashboardFallback() {
    if (document.getElementById("live-dashboard-root")) return;
    if (document.querySelector("script[data-preview-dashboard-fallback]")) return;

    const script = document.createElement("script");
    script.src = "../nowcast_service/static/local-live.js";
    script.dataset.previewDashboardFallback = "true";
    document.head.append(script);
  }

  function placePreviewChrome() {
    const dashboard = document.getElementById("live-dashboard-root");
    if (!dashboard) return false;

    const app = dashboard.closest(".app");
    if (banner && app && banner.nextElementSibling !== app) {
      document.body.insertBefore(banner, app);
    }
    if (controls && dashboard.nextElementSibling !== controls) {
      dashboard.insertAdjacentElement("afterend", controls);
    }
    return true;
  }

  function waitForDashboard() {
    if (placePreviewChrome()) return;
    window.setTimeout(waitForDashboard, 50);
  }

  ensureForecastShell();
  window.setTimeout(loadDashboardFallback, 0);

  for (const button of document.querySelectorAll("[data-preview-state]")) {
    button.addEventListener("click", () => {
      const state = button.getAttribute("data-preview-state");
      if (!state) return;
      window.__awcPreviewSetState?.(state);
      for (const item of document.querySelectorAll("[data-preview-state]")) {
        item.setAttribute("aria-pressed", String(item === button));
      }
    });
  }

  document.getElementById("preview-disconnect")?.addEventListener("click", () => {
    window.__awcPreviewDisconnect?.();
  });

  document
    .querySelector("[data-preview-state='GREEN']")
    ?.setAttribute("aria-pressed", "true");

  waitForDashboard();
})();
