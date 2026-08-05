(() => {
  "use strict";

  const FORECAST_HOSTS = new Set([
    "api.open-meteo.com",
    "ensemble-api.open-meteo.com",
  ]);
  const GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search";
  const DEFAULT_LOCATION = Object.freeze({
    name: "Geiselhöring",
    latitude: 48.84,
    longitude: 12.40,
  });
  const BAVARIA_BOUNDS = Object.freeze({
    minimumLatitude: 47.0,
    maximumLatitude: 50.75,
    minimumLongitude: 8.75,
    maximumLongitude: 14.05,
  });
  const REQUEST_TIMEOUT_MS =
    Number(window.__AWC_FORECAST_REQUEST_TIMEOUT_MS) || 10000;
  const TOTAL_LOADING_LIMIT_MS =
    Number(window.__AWC_FORECAST_TOTAL_TIMEOUT_MS) || 45000;
  const originalFetch = window.fetch.bind(window);
  const activeControllers = new Set();
  let selectedLocation = loadStoredLocation();
  let locationConfirmed = true;
  let loadingStartedAt = null;
  let watchdogTriggered = false;
  let syncQueued = false;
  let syncRuns = 0;

  function byId(id) {
    return document.getElementById(id);
  }

  function setText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function setHtml(node, value) {
    if (node && node.innerHTML !== value) node.innerHTML = value;
  }

  function setDisabled(node, value) {
    if (node && node.disabled !== value) node.disabled = value;
  }

  function setHidden(node, value) {
    if (node && node.hidden !== value) node.hidden = value;
  }

  function setTitle(node, value) {
    if (node && node.title !== value) node.title = value;
  }

  function insideBavaria(latitude, longitude) {
    return latitude >= BAVARIA_BOUNDS.minimumLatitude
      && latitude <= BAVARIA_BOUNDS.maximumLatitude
      && longitude >= BAVARIA_BOUNDS.minimumLongitude
      && longitude <= BAVARIA_BOUNDS.maximumLongitude;
  }

  function loadStoredLocation() {
    try {
      const parsed = JSON.parse(
        window.localStorage.getItem("awc-forecast-location") || "null",
      );
      if (
        parsed
        && typeof parsed.name === "string"
        && Number.isFinite(parsed.latitude)
        && Number.isFinite(parsed.longitude)
        && insideBavaria(parsed.latitude, parsed.longitude)
      ) {
        return parsed;
      }
    } catch {
      // Invalid local storage must never block the default location.
    }
    return { ...DEFAULT_LOCATION };
  }

  function saveLocation(location) {
    selectedLocation = location;
    locationConfirmed = true;
    try {
      window.localStorage.setItem(
        "awc-forecast-location",
        JSON.stringify(location),
      );
    } catch {
      // Persistence is optional.
    }
    window.dispatchEvent(
      new CustomEvent("awc:forecast-location", { detail: location }),
    );
    updateLocationCopy();
    updateStartAvailability();
  }

  function requestUrl(input) {
    const raw = typeof input === "string" ? input : input?.url;
    return new URL(raw, window.location.href);
  }

  function isForecastRequest(input) {
    try {
      return FORECAST_HOSTS.has(requestUrl(input).hostname);
    } catch {
      return false;
    }
  }

  function withSelectedLocation(input) {
    const url = requestUrl(input);
    url.searchParams.set("latitude", String(selectedLocation.latitude));
    url.searchParams.set("longitude", String(selectedLocation.longitude));
    if (typeof input === "string") return url.toString();
    return new Request(url.toString(), input);
  }

  function guardedForecastFetch(input, init = {}) {
    if (!isForecastRequest(input)) return originalFetch(input, init);
    if (!locationConfirmed) {
      return Promise.reject(
        new Error("Forecast-Ort wurde noch nicht bestätigt."),
      );
    }

    const controller = new AbortController();
    const upstream = init.signal;
    const relayAbort = () => controller.abort(upstream?.reason);
    if (upstream) {
      if (upstream.aborted) relayAbort();
      else upstream.addEventListener("abort", relayAbort, { once: true });
    }

    activeControllers.add(controller);
    const timeout = window.setTimeout(() => {
      controller.abort(
        new DOMException(
          "Forecast-Quelle hat das feste Zeitlimit überschritten.",
          "TimeoutError",
        ),
      );
    }, REQUEST_TIMEOUT_MS);

    queueSync();
    return originalFetch(withSelectedLocation(input), {
      ...init,
      signal: controller.signal,
    }).finally(() => {
      window.clearTimeout(timeout);
      activeControllers.delete(controller);
      upstream?.removeEventListener?.("abort", relayAbort);
      queueSync();
    });
  }

  window.fetch = guardedForecastFetch;

  function abortForecastRequests() {
    for (const controller of activeControllers) {
      controller.abort(
        new DOMException("Forecast-Abruf abgebrochen.", "AbortError"),
      );
    }
    activeControllers.clear();
    queueSync();
  }

  window.AstroWolkencheckForecastNetwork = Object.freeze({
    abortAll: abortForecastRequests,
    activeCount: () => activeControllers.size,
    selectedLocation: () => ({ ...selectedLocation }),
    getState: () => ({
      activeRequests: activeControllers.size,
      loading: isForecastLoading(),
      locationConfirmed,
      selectedLocation: { ...selectedLocation },
      syncRuns,
    }),
  });

  function forecastPlaceholder() {
    return document.querySelector(
      "#overviewContent .awc-forecast-placeholder",
    );
  }

  function ensureForecastPlaceholder() {
    const target = byId("overviewContent");
    if (!target) return null;
    const existingRealHero = target.querySelector(
      ".decision-hero:not(.awc-forecast-placeholder)",
    );
    if (existingRealHero) return null;

    let placeholder = forecastPlaceholder();
    if (placeholder) return placeholder;

    placeholder = document.createElement("section");
    placeholder.className = "decision-hero awc-forecast-placeholder";
    placeholder.dataset.tone = "neutral";
    placeholder.setAttribute(
      "aria-labelledby",
      "awc-forecast-placeholder-title",
    );
    placeholder.innerHTML = `
      <div class="decision-copy">
        <div class="eyebrow">Astronomischer Forecast</div>
        <h2 id="awc-forecast-placeholder-title">Forecast noch nicht gestartet</h2>
        <p id="awc-forecast-placeholder-detail">Zeitraum und Forecast-Ort prüfen und anschließend „Forecast laden“ wählen. Vorher werden keine externen Wettermodelle abgefragt.</p>
      </div>
      <div class="decision-facts">
        <div><span>Status</span><strong id="awc-forecast-placeholder-status">bereit</strong></div>
        <div><span>Forecast-Ort</span><strong id="awc-forecast-placeholder-location"></strong></div>
        <div><span>Netzwerk</span><strong id="awc-forecast-placeholder-network">wartet auf Start</strong></div>
      </div>
    `;
    target.replaceChildren(placeholder);
    updateLocationCopy();
    return placeholder;
  }

  function updateTone(placeholder, tone) {
    const normalized = ["good", "warn", "bad"].includes(tone)
      ? tone
      : "neutral";
    if (placeholder.dataset.tone === normalized) return;
    placeholder.classList.remove("good", "warn", "bad");
    if (normalized !== "neutral") placeholder.classList.add(normalized);
    placeholder.dataset.tone = normalized;
  }

  function updatePlaceholder({
    title,
    detail,
    status,
    network,
    tone = "neutral",
  }) {
    const placeholder = ensureForecastPlaceholder();
    if (!placeholder) return;
    updateTone(placeholder, tone);
    setText(byId("awc-forecast-placeholder-title"), title);
    setText(byId("awc-forecast-placeholder-detail"), detail);
    setText(byId("awc-forecast-placeholder-status"), status);
    setText(byId("awc-forecast-placeholder-network"), network);
    updateLocationCopy();
  }

  function ensureLocationControls() {
    const controls = document.querySelector(".app-header .controls");
    const refresh = byId("refreshBtn");
    if (!controls || !refresh || byId("awc-location-control")) return;

    const wrapper = document.createElement("div");
    wrapper.id = "awc-location-control";
    wrapper.className = "awc-location-control";
    wrapper.innerHTML = `
      <label for="awc-location-query">Forecast- und Satellitenort</label>
      <div class="awc-location-search-row">
        <input id="awc-location-query" type="search" autocomplete="off" value="${selectedLocation.name}" aria-describedby="awc-location-status">
        <button id="awc-location-search" type="button">Ort suchen</button>
      </div>
      <select id="awc-location-results" aria-label="Gefundenen Ort auswählen" hidden></select>
      <small id="awc-location-status"></small>
    `;
    controls.insertBefore(wrapper, refresh);

    const query = byId("awc-location-query");
    const search = byId("awc-location-search");
    const results = byId("awc-location-results");
    query?.addEventListener("input", () => {
      locationConfirmed = query.value.trim() === selectedLocation.name;
      updateLocationCopy();
      updateStartAvailability();
    });
    query?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        searchLocation();
      }
    });
    search?.addEventListener("click", searchLocation);
    results?.addEventListener("change", () => {
      const option = results.selectedOptions[0];
      if (!option?.dataset.location) return;
      const location = JSON.parse(option.dataset.location);
      if (query.value !== location.name) query.value = location.name;
      setHidden(results, true);
      saveLocation(location);
    });
    updateLocationCopy();
  }

  async function searchLocation() {
    const query = byId("awc-location-query");
    const results = byId("awc-location-results");
    const status = byId("awc-location-status");
    const button = byId("awc-location-search");
    const name = query?.value.trim() || "";
    if (!query || !results || !status || !button) return;
    if (name.length < 2) {
      setText(
        status,
        "Bitte mindestens zwei Zeichen oder eine Postleitzahl eingeben.",
      );
      return;
    }

    setDisabled(button, true);
    setText(status, "Ort wird gesucht …");
    setHidden(results, true);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    try {
      const url = new URL(GEOCODING_URL);
      url.searchParams.set("name", name);
      url.searchParams.set("count", "10");
      url.searchParams.set("language", "de");
      url.searchParams.set("format", "json");
      url.searchParams.set("country_code", "DE");
      const response = await originalFetch(url, {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      const matches = (payload.results || []).filter(
        (item) => Number.isFinite(item.latitude)
          && Number.isFinite(item.longitude)
          && insideBavaria(item.latitude, item.longitude),
      );
      if (!matches.length) {
        setText(
          status,
          "Kein passender Ort im Bayern-Ausschnitt gefunden.",
        );
        return;
      }
      results.replaceChildren(
        ...matches.map((item, index) => {
          const location = {
            name: [item.name, item.admin2, item.admin1]
              .filter(Boolean)
              .filter(
                (part, position, values) => values.indexOf(part) === position,
              )
              .join(", "),
            latitude: item.latitude,
            longitude: item.longitude,
          };
          const option = document.createElement("option");
          option.value = String(index);
          option.textContent = location.name;
          option.dataset.location = JSON.stringify(location);
          return option;
        }),
      );
      setHidden(results, false);
      results.selectedIndex = 0;
      setText(
        status,
        "Treffer auswählen; erst danach kann der Forecast starten.",
      );
    } catch (error) {
      console.warn("Ortssuche fehlgeschlagen", error);
      setText(
        status,
        "Ortssuche fehlgeschlagen. Geiselhöring bleibt ausgewählt.",
      );
    } finally {
      window.clearTimeout(timeout);
      setDisabled(button, false);
    }
  }

  function updateLocationCopy() {
    setText(
      byId("awc-forecast-placeholder-location"),
      selectedLocation.name,
    );
    const statusCopy = locationConfirmed
      ? `Ausgewählt: ${selectedLocation.name}. Live-Sicherheit bleibt Geiselhöring.`
      : "Eingabe geändert. Bitte den Ort suchen und einen Treffer auswählen.";
    setText(byId("awc-location-status"), statusCopy);

    const noteCopy = `
        <strong>Kein automatischer Forecast-Abruf.</strong>
        <span>Zeitraum und Ort prüfen, dann bewusst starten. Forecast/Satellit: ${selectedLocation.name}. Live-Sicherheit: Geiselhöring.</span>
      `;
    setHtml(byId("awc-forecast-start-note"), noteCopy);
  }

  function ensureStartNote() {
    const controls = document.querySelector(".app-header .controls");
    if (!controls || byId("awc-forecast-start-note")) return;
    const note = document.createElement("div");
    note.id = "awc-forecast-start-note";
    note.className = "awc-forecast-start-note";
    controls.append(note);
    updateLocationCopy();
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
        detail:
          "Offene externe Wetteranfragen wurden beendet. Die lokale Live-Sicherheit bleibt aktiv.",
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
    if (
      !root
      || !liveGrid
      || liveGrid.dataset.compactLayout === "true"
    ) {
      return;
    }

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
    return document
      .querySelector("#cacheNotice .notice.error")
      ?.textContent?.trim() || "";
  }

  function isForecastLoading() {
    const progress = byId("requestProgress");
    return Boolean(progress && !progress.hidden) || activeControllers.size > 0;
  }

  function periodIsValid() {
    const start = new Date(byId("startInput")?.value || "");
    const end = new Date(byId("endInput")?.value || "");
    return Number.isFinite(start.getTime())
      && Number.isFinite(end.getTime())
      && end > start;
  }

  function updateStartAvailability() {
    const refresh = byId("refreshBtn");
    if (!refresh || isForecastLoading()) return;
    const allowed = locationConfirmed && periodIsValid();
    setDisabled(refresh, !allowed);
    setTitle(
      refresh,
      allowed
        ? `Forecast für ${selectedLocation.name} laden`
        : "Zeitraum und Forecast-Ort zuerst bestätigen",
    );
  }

  function syncForecastUi() {
    syncQueued = false;
    syncRuns += 1;
    restructureDashboard();
    ensureLocationControls();
    ensureStartNote();
    ensureCancelButton();

    const refresh = byId("refreshBtn");
    const cancel = byId("awc-cancel-forecast");
    const realHero = document.querySelector(
      "#overviewContent .decision-hero:not(.awc-forecast-placeholder)",
    );
    if (!refresh) return;

    const loading = isForecastLoading();
    if (loading) {
      if (loadingStartedAt === null) loadingStartedAt = Date.now();
      setDisabled(refresh, true);
      setHidden(cancel, false);
      updatePlaceholder({
        title: "Forecast wird geladen",
        detail: `${currentProgressText()}. Langsame oder nicht antwortende Quellen werden nach spätestens 10 Sekunden pro Anfrage beendet.`,
        status: currentProgressText(),
        network: `${activeControllers.size} externe Anfragen aktiv`,
        tone: "warn",
      });
      return;
    }

    loadingStartedAt = null;
    watchdogTriggered = false;
    setHidden(cancel, true);
    setText(refresh, "Forecast laden");
    updateStartAvailability();
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
      detail: locationConfirmed
        ? "Zeitraum und Forecast-Ort sind vorbereitet. Erst „Forecast laden“ startet externe Wettermodelle."
        : "Bitte einen gefundenen Forecast-Ort auswählen. Vorher werden keine externen Wettermodelle abgefragt.",
      status: locationConfirmed ? "bereit" : "Ort auswählen",
      network: "wartet auf Start",
      tone: "neutral",
    });
  }

  function queueSync() {
    if (syncQueued) return;
    syncQueued = true;
    window.requestAnimationFrame(syncForecastUi);
  }

  function observeUiChanges() {
    const bodyObserver = new MutationObserver(queueSync);
    bodyObserver.observe(document.body, {
      childList: true,
      subtree: true,
    });

    const progress = byId("requestProgress");
    if (progress) {
      new MutationObserver(queueSync).observe(progress, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: ["hidden"],
      });
    }

    const cache = byId("cacheNotice");
    if (cache) {
      new MutationObserver(queueSync).observe(cache, {
        childList: true,
        subtree: true,
        characterData: true,
      });
    }
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
          detail:
            "Der Gesamtabruf hat das feste Zeitlimit überschritten. Bitte erneut versuchen; die lokale Live-Sicherheit war davon nicht betroffen.",
          status: "Zeitlimit erreicht",
          network: "offene Anfragen beendet",
          tone: "bad",
        });
      }
    }, 1000);
  }

  function init() {
    const refresh = byId("refreshBtn");
    ensureForecastPlaceholder();
    ensureLocationControls();
    ensureStartNote();
    ensureCancelButton();
    restructureDashboard();
    updateLocationCopy();
    window.dispatchEvent(
      new CustomEvent("awc:forecast-location", {
        detail: selectedLocation,
      }),
    );

    observeUiChanges();
    byId("startInput")?.addEventListener("change", queueSync);
    byId("endInput")?.addEventListener("change", queueSync);
    refresh?.addEventListener(
      "click",
      (event) => {
        if (!locationConfirmed || !periodIsValid()) {
          event.preventDefault();
          event.stopImmediatePropagation();
          updatePlaceholder({
            title: "Forecast noch nicht gestartet",
            detail:
              "Zeitraum und einen gefundenen Forecast-Ort zuerst bestätigen.",
            status: "Eingaben fehlen",
            network: "keine Anfrage gestartet",
            tone: "warn",
          });
          return;
        }
        queueSync();
      },
      true,
    );
    startWatchdog();
    queueSync();
  }

  if (document.readyState === "loading") {
    document.addEventListener(
      "DOMContentLoaded",
      () => window.setTimeout(init, 0),
      { once: true },
    );
  } else {
    window.setTimeout(init, 0);
  }
})();
