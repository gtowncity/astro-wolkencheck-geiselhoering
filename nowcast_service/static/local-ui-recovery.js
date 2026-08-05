(() => {
  "use strict";

  const FORECAST_HOSTS = new Set([
    "api.open-meteo.com",
    "ensemble-api.open-meteo.com",
  ]);
  const REQUEST_TIMEOUT_MS = Number(window.__AWC_FORECAST_REQUEST_TIMEOUT_MS) || 10000;
  const TOTAL_LOADING_LIMIT_MS = Number(window.__AWC_FORECAST_TOTAL_TIMEOUT_MS) || 45000;
  const originalFetch = window.fetch.bind(window);
  const activeControllers = new Set();
  let loadingStartedAt = null;
  let watchdogTriggered = false;
  let syncQueued = false;

  function isForecastRequest(input) {
    try {
      const raw = typeof input === "string" ? input : input?.url;
      return FORECAST_HOSTS.has(new URL(raw, window.location.href).hostname);
    } catch {
      return false;
    }
  }

  function guardedForecastFetch(input, init = {}) {
    if (!isForecastRequest(input)) return originalFetch(input, init);

    const controller = new AbortController();
    const upstream = init.signal;
    const relayAbort = () => controller.abort(upstream?.reason);
    if (upstream) {
      if (upstream.aborted) relayAbort();
      else upstream.addEventListener("abort", relayAbort, { once: true });
    }

    activeControllers.add(controller);
    const timeout = window.setTimeout(() => {
      controller.abort(new DOMException(
        "Forecast-Quelle hat das feste Zeitlimit überschritten.",
        "TimeoutError",
      ));
    }, REQUEST_TIMEOUT_MS);

    return originalFetch(input, { ...init, signal: controller.signal })
      .finally(() => {
        window.clearTimeout(timeout);
        activeControllers.delete(controller);
        upstream?.removeEventListener?.("abort", relayAbort);
      });
  }

  window.fetch = guardedForecastFetch;

  function abortForecastRequests() {
    for (const controller of activeControllers) {
      controller.abort(new DOMException("Forecast-Abruf abgebrochen.", "AbortError"));
    }
    activeControllers.clear();
  }

  window.AstroWolkencheckForecastNetwork = Object.freeze({
    abortAll: abortForecastRequests,
    activeCount: () => activeControllers.size,
  });

  function byId(id) {
    return document.getElementById(id);
  }

  function forecastPlaceholder() {
    return document.querySelector("#overviewContent .awc-forecast-placeholder");
  }

  function ensureForecastPlaceholder() {
    const target = byId("overviewContent");
    if (!target) return null;
    const existingRealHero = target.querySelector(".decision-hero:not(.awc-forecast-placeholder)");
    if (existingRealHero) return null;

    let placeholder = forecastPlaceholder();
    if (placeholder) return placeholder;

    placeholder = document.createElement("section");
    placeholder.className = "decision-hero awc-forecast-placeholder";
    placeholder.setAttribute("aria-labelledby", "awc-forecast-placeholder-title");
    placeholder.innerHTML = `
      <div class="decision-copy">
        <div class="eyebrow">Astronomischer Forecast</div>
        <h2 id="awc-forecast-placeholder-title">Forecast noch nicht gestartet</h2>
        <p id="awc-forecast-placeholder-detail">Zeitraum prüfen und anschließend „Forecast laden“ wählen. Vorher werden keine externen Wettermodelle abgefragt.</p>
      </div>
      <div class="decision-facts">
        <div><span>Status</span><strong id="awc-forecast-placeholder-status">bereit</strong></div>
        <div><span>Ort</span><strong>Geiselhöring</strong></div>
        <div><span>Netzwerk</span><strong id="awc-forecast-placeholder-network">wartet auf Start</strong></div>
      </div>
    `;
    target.replaceChildren(placeholder);
    return placeholder;
  }

  function updatePlaceholder({ title, detail, status, network, tone = "neutral" }) {
    const placeholder = ensureForecastPlaceholder();
    if (!placeholder) return;
    placeholder.classList.remove("good", "warn", "bad");
    if (["good", "warn", "bad"].includes(tone)) placeholder.classList.add(tone);
    const values = {
      "awc-forecast-placeholder-title": title,
      "awc-forecast-placeholder-detail": detail,
      "awc-forecast-placeholder-status": status,
      "awc-forecast-placeholder-network": network,
    };
    for (const [id, value] of Object.entries(values)) {
      const node = byId(id);
      if (node) node.textContent = value;
    }
  }

  function ensureStartNote() {
    const controls = document.querySelector(".app-header .controls");
    if (!controls || byId("awc-forecast-start-note")) return;
    const note = document.createElement("div");
    note.id = "awc-forecast-start-note";
    note.className = "awc-forecast-start-note";
    note.innerHTML = `
      <strong>Kein automatischer Forecast-Abruf.</strong>
      <span>Zeitraum prüfen, dann bewusst starten. Ort: Geiselhöring.</span>
    `;
    controls.append(note);
  }

  function ensureCancelButton() {
    const refresh = byId("refreshBtn");
    if (!refresh || byId("awc-cancel-forecast")) return;
    const cancel = document.createElement("button");
    cancel.id = "awc-cancel-forecast";
    cancel.type = "button";
    cancel.className = "awc-cancel-forecast";
    cancel.textContent = "Forecast abbrechen";
    cancel.hidden = true;
    cancel.addEventListener("click", () => {
      abortForecastRequests();
      updatePlaceholder({
        title: "Forecast-Abruf wird abgebrochen",
        detail: "Offene externe Wetteranfragen wurden beendet. Die lokale Live-Sicherheit bleibt aktiv.",
        status: "Abbruch angefordert",
        network: "Anfragen werden beendet",
        tone: "warn",
      });
    });
    refresh.insertAdjacentElement("afterend", cancel);
  }

  function restructureDashboard() {
    const root = byId("live-dashboard-root");
    const liveGrid = root?.querySelector(".awc-live-grid");
    const secondary = root?.querySelector(".awc-secondary-grid");
    if (!root || !liveGrid || liveGrid.dataset.compactLayout === "true") return;

    const radar = liveGrid.querySelector(".awc-radar-card");
    const side = liveGrid.querySelector(".awc-side-stack");
    const change = secondary?.querySelector(".awc-change-card");
    const alarm = secondary?.querySelector(".awc-alarm-card");
    if (!radar || !side || !change || !alarm) return;

    const main = document.createElement("div");
    main.className = "awc-main-stack";
    main.append(radar, change, alarm);
    liveGrid.replaceChildren(main, side);
    secondary.remove();
    liveGrid.dataset.compactLayout = "true";
  }

  function currentProgressText() {
    return byId("progressMetric")?.textContent?.trim()
      || "Externe Wettermodelle werden abgefragt.";
  }

  function visibleForecastError() {
    return document.querySelector("#cacheNotice .notice.error")?.textContent?.trim() || "";
  }

  function syncForecastUi() {
    syncQueued = false;
    restructureDashboard();
    ensureStartNote();
    ensureCancelButton();

    const refresh = byId("refreshBtn");
    const cancel = byId("awc-cancel-forecast");
    const realHero = document.querySelector("#overviewContent .decision-hero:not(.awc-forecast-placeholder)");
    if (!refresh) return;

    const loading = refresh.disabled;
    if (loading) {
      if (loadingStartedAt === null) loadingStartedAt = Date.now();
      if (cancel) cancel.hidden = false;
      updatePlaceholder({
        title: "Forecast wird geladen",
        detail: `${currentProgressText()}. Langsame oder nicht antwortende Quellen werden automatisch nach spätestens 10 Sekunden pro Anfrage beendet.`,
        status: currentProgressText(),
        network: `${activeControllers.size} externe Anfragen aktiv`,
        tone: "warn",
      });
      return;
    }

    loadingStartedAt = null;
    watchdogTriggered = false;
    if (cancel) cancel.hidden = true;
    if (realHero) return;

    const error = visibleForecastError();
    if (error) {
      updatePlaceholder({
        title: "Forecast konnte nicht vollständig geladen werden",
        detail: error,
        status: "Abruf beendet",
        network: "Erneuter Start möglich",
        tone: "bad",
      });
      return;
    }

    updatePlaceholder({
      title: "Forecast noch nicht gestartet",
      detail: "Zeitraum prüfen und anschließend „Forecast laden“ wählen. Vorher werden keine externen Wettermodelle abgefragt.",
      status: "bereit",
      network: "wartet auf Start",
      tone: "neutral",
    });
  }

  function queueSync() {
    if (syncQueued) return;
    syncQueued = true;
    queueMicrotask(syncForecastUi);
  }

  function startWatchdog() {
    window.setInterval(() => {
      queueSync();
      if (
        loadingStartedAt !== null
        && !watchdogTriggered
        && Date.now() - loadingStartedAt > TOTAL_LOADING_LIMIT_MS
      ) {
        watchdogTriggered = true;
        abortForecastRequests();
        updatePlaceholder({
          title: "Forecast-Abruf automatisch beendet",
          detail: "Der Gesamtabruf hat das Sicherheitszeitlimit überschritten. Bitte erneut versuchen; die lokale Live-Sicherheit war davon nicht betroffen.",
          status: "Zeitlimit erreicht",
          network: "offene Anfragen beendet",
          tone: "bad",
        });
      }
    }, 1000);
  }

  function init() {
    const refresh = byId("refreshBtn");
    if (refresh && !refresh.disabled) refresh.textContent = "Forecast laden";
    ensureForecastPlaceholder();
    ensureStartNote();
    ensureCancelButton();
    restructureDashboard();

    const observer = new MutationObserver(queueSync);
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["disabled", "hidden", "class"],
    });
    byId("startInput")?.addEventListener("change", queueSync);
    byId("endInput")?.addEventListener("change", queueSync);
    refresh?.addEventListener("click", queueSync);
    startWatchdog();
    queueSync();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => window.setTimeout(init, 0), { once: true });
  } else {
    window.setTimeout(init, 0);
  }
})();
