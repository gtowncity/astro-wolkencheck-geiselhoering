(() => {
  "use strict";

  const API = "/api/v1/satellite";
  const EUMETVIEW_WMS = "https://view.eumetsat.int/geoserver/wms";
  const BBOX = "47.0,8.75,50.75,14.05";
  const METADATA_REFRESH_MS = 120000;
  const FRAME_DURATION_MS = 500;
  const PREVIEW_WIDTH = 1200;
  const PREVIEW_HEIGHT = 850;
  const HD_WIDTH = 2400;
  const HD_HEIGHT = 1700;
  const PREFETCH_CONCURRENCY = 4;
  const MAX_MOUNT_ATTEMPTS = 40;
  const state = {
    enabled: false,
    product: "cloudtype",
    metadata: null,
    frames: [],
    index: -1,
    playing: false,
    playbackGeneration: 0,
    refreshTimer: null,
    scale: 1,
    translateX: 0,
    translateY: 0,
    pointerId: null,
    pointerX: 0,
    pointerY: 0,
    location: null,
    loadingMetadata: false,
    globalEventsBound: false,
    mountQueued: false,
    mountAttempts: 0,
    imageObjectUrl: null,
    loadSerial: 0,
    previewPromises: new Map(),
    prefetchGeneration: 0,
  };

  const byId = (id) => document.getElementById(id);

  function setText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function setDisabled(node, value) {
    if (node && node.disabled !== value) node.disabled = value;
  }

  function setHidden(node, value) {
    if (node && node.hidden !== value) node.hidden = value;
  }

  function parsedDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatTime(value) {
    const date = parsedDate(value);
    if (!date) return "unbekannte Zeit";
    const local = new Intl.DateTimeFormat("de-DE", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
    const utc = new Intl.DateTimeFormat("de-DE", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "UTC",
    }).format(date);
    return `${local} Ortszeit (${utc} UTC)`;
  }

  function freshnessLimitMinutes() {
    const value = Number(state.metadata?.freshnessLimitMinutes);
    return Number.isFinite(value) && value >= 0 ? value : 20;
  }

  function productInfo(key = state.product) {
    const products = Array.isArray(state.metadata?.products) ? state.metadata.products : [];
    return products.find((product) => product.key === key)
      || (state.metadata?.selectedProduct?.key === key ? state.metadata.selectedProduct : null);
  }

  function productTitle(key) {
    return productInfo(key)?.title || key || "unbekanntes Produkt";
  }

  function productMode(key) {
    if (key === "infrared") return "grau · Tag/Nacht";
    if (key === "geocolour") return "farbig · Tag/Nacht";
    if (key === "cloudtype" || key === "cloudphase") return "farbig · Tag";
    if (key === "lightning") return "Blitze";
    return "";
  }

  function clearImageObjectUrl() {
    if (!state.imageObjectUrl) return;
    URL.revokeObjectURL(state.imageObjectUrl);
    state.imageObjectUrl = null;
  }

  function localImageUrl(frame, latest = false) {
    const params = new URLSearchParams({ product: state.product });
    if (latest) {
      params.set("_live", String(Date.now()));
    } else {
      params.set("time", frame);
    }
    if (state.location) {
      params.set("latitude", String(state.location.latitude));
      params.set("longitude", String(state.location.longitude));
      params.set("location_name", state.location.name);
    }
    return `${API}/image?${params.toString()}`;
  }

  function historicalWmsUrl(frame, { hd = true } = {}) {
    const info = productInfo();
    if (!info?.layer) return localImageUrl(frame, false);
    const lightning = state.product === "lightning";
    const params = new URLSearchParams({
      service: "WMS",
      version: "1.3.0",
      request: "GetMap",
      layers: info.layer,
      styles: "",
      crs: "EPSG:4326",
      bbox: BBOX,
      width: String(hd ? HD_WIDTH : PREVIEW_WIDTH),
      height: String(hd ? HD_HEIGHT : PREVIEW_HEIGHT),
      format: lightning ? "image/png" : "image/jpeg",
      bgcolor: lightning ? "0x000000" : "0xCCCCCC",
      time: frame,
    });
    return `${EUMETVIEW_WMS}?${params.toString()}`;
  }

  function viewerMarkup() {
    return `
      <section id="awc-satellite-viewer" class="awc-satellite-viewer" data-enabled="false" data-playing="false" aria-labelledby="awc-satellite-title">
        <header class="awc-satellite-toolbar">
          <div>
            <span class="awc-satellite-kicker">ECHTES SATELLITENBILD</span>
            <strong id="awc-satellite-title">EUMETSAT Meteosat-12</strong>
          </div>
          <div class="awc-satellite-actions">
            <label>
              <span>Produkt</span>
              <select id="awc-satellite-product" aria-label="Satellitenprodukt auswählen" disabled>
                <option value="cloudtype">Nach Forecast-Start verfügbar</option>
              </select>
            </label>
            <button id="awc-satellite-refresh" type="button" title="Aufnahmen neu laden" disabled>↻</button>
            <button id="awc-satellite-fullscreen" type="button" disabled>Vollbild</button>
          </div>
        </header>
        <div id="awc-satellite-status" class="awc-satellite-status" data-tone="WAITING">
          Wartet auf Zeitraum, Ort und „Forecast laden“.
        </div>
        <div id="awc-satellite-viewport" class="awc-satellite-viewport" tabindex="0" aria-label="Satellitenbild. Nach dem Forecast-Start mit Mausrad oder Plus und Minus zoomen; im Zoom ziehen.">
          <img id="awc-satellite-image" alt="Echtes EUMETSAT-Satellitenbild von Bayern mit Ortsmarkierung" draggable="false">
          <div id="awc-satellite-pin-layer" class="awc-satellite-pin-layer" hidden aria-hidden="true">
            <span id="awc-satellite-pin" class="awc-satellite-pin"></span>
            <span id="awc-satellite-pin-label" class="awc-satellite-pin-label"></span>
          </div>
          <div id="awc-satellite-provenance" class="awc-satellite-provenance" hidden></div>
          <div id="awc-satellite-placeholder" class="awc-satellite-placeholder">Noch kein Satellitenabruf. Zuerst Zeitraum und Ort festlegen, dann „Forecast laden“ wählen.</div>
          <div class="awc-satellite-zoom" aria-label="Zoomsteuerung">
            <button id="awc-satellite-zoom-out" type="button" aria-label="Herauszoomen" disabled>−</button>
            <button id="awc-satellite-zoom-reset" type="button" aria-label="Zoom zurücksetzen" disabled>100 %</button>
            <button id="awc-satellite-zoom-in" type="button" aria-label="Hineinzoomen" disabled>+</button>
          </div>
        </div>
        <footer class="awc-satellite-timeline">
          <button id="awc-satellite-prev" type="button" aria-label="Vorherige Aufnahme" disabled>◀</button>
          <button id="awc-satellite-play" type="button" aria-label="Zeitverlauf abspielen" disabled>▶ Abspielen</button>
          <input id="awc-satellite-range" type="range" min="0" max="0" value="0" step="1" aria-label="Aufnahmezeit auswählen" disabled>
          <button id="awc-satellite-next" type="button" aria-label="Nächste Aufnahme" disabled>▶</button>
          <strong id="awc-satellite-time">wartet auf Start</strong>
        </footer>
        <p class="awc-satellite-note">RGB-Farben zeigen Wolkentyp, Wolkenphase und optische Eigenschaften – Niederschlag selbst kommt aus dem DWD-Radar. Historische Frames werden für flüssiges Abspielen direkt von EUMETView vorgeladen; Einzelbilder werden in HD geladen. Uhrzeiten stehen als Ortszeit plus UTC.</p>
      </section>
    `;
  }

  function queueMount(delay = 0) {
    if (state.mountQueued) return;
    state.mountQueued = true;
    window.setTimeout(() => {
      state.mountQueued = false;
      if (mount()) {
        state.mountAttempts = 0;
        return;
      }
      state.mountAttempts += 1;
      if (state.mountAttempts < MAX_MOUNT_ATTEMPTS) queueMount(100);
    }, delay);
  }

  function mount() {
    const existing = byId("awc-satellite-viewer");
    if (existing) {
      updateControlAvailability();
      return true;
    }
    const host = document.querySelector(".awc-radar-visual");
    if (!host) return false;

    stopPlayback({ reloadHd: false });
    clearImageObjectUrl();
    host.classList.add("awc-satellite-host");
    host.removeAttribute("role");
    host.removeAttribute("aria-label");
    host.innerHTML = viewerMarkup();
    resetTransform();
    bindLocalEvents();
    bindGlobalEvents();
    updateControlAvailability();
    if (state.enabled) {
      startRefreshTimer();
      loadMetadata(state.product, { keepLatest: true, force: true });
    }
    return true;
  }

  function bindLocalEvents() {
    byId("awc-satellite-product")?.addEventListener("change", (event) => {
      if (!state.enabled) return;
      stopPlayback({ reloadHd: false });
      state.product = event.target.value;
      state.previewPromises.clear();
      state.prefetchGeneration += 1;
      resetTransform();
      loadMetadata(state.product, { keepLatest: true, force: true });
    });
    byId("awc-satellite-refresh")?.addEventListener("click", () => {
      if (!state.enabled) return;
      stopPlayback({ reloadHd: false });
      state.previewPromises.clear();
      state.prefetchGeneration += 1;
      loadMetadata(state.product, { keepLatest: true, force: true });
    });
    byId("awc-satellite-fullscreen")?.addEventListener("click", toggleFullscreen);
    byId("awc-satellite-play")?.addEventListener("click", togglePlayback);
    byId("awc-satellite-prev")?.addEventListener("click", () => showFrame(state.index - 1));
    byId("awc-satellite-next")?.addEventListener("click", () => showFrame(state.index + 1));
    byId("awc-satellite-range")?.addEventListener("input", (event) => {
      stopPlayback({ reloadHd: false });
      showFrame(Number(event.target.value));
    });
    byId("awc-satellite-zoom-in")?.addEventListener("click", () => zoomBy(0.4));
    byId("awc-satellite-zoom-out")?.addEventListener("click", () => zoomBy(-0.4));
    byId("awc-satellite-zoom-reset")?.addEventListener("click", resetTransform);

    const viewport = byId("awc-satellite-viewport");
    viewport?.addEventListener("wheel", onWheel, { passive: false });
    viewport?.addEventListener("pointerdown", onPointerDown);
    viewport?.addEventListener("pointermove", onPointerMove);
    viewport?.addEventListener("pointerup", onPointerUp);
    viewport?.addEventListener("pointercancel", onPointerUp);
    viewport?.addEventListener("dblclick", resetTransform);
  }

  function validLocation(detail) {
    return detail && Number.isFinite(detail.latitude) && Number.isFinite(detail.longitude);
  }

  function applyLocation(detail) {
    if (!validLocation(detail)) return false;
    state.location = {
      name: String(detail.name || "Ausgewählter Ort"),
      latitude: detail.latitude,
      longitude: detail.longitude,
    };
    updateHistoricalOverlay();
    return true;
  }

  function bindGlobalEvents() {
    if (state.globalEventsBound) return;
    state.globalEventsBound = true;
    document.addEventListener("fullscreenchange", updateFullscreenButton);
    window.addEventListener("awc:forecast-location", (event) => {
      if (!applyLocation(event.detail)) return;
      if (state.enabled && state.index >= 0 && byId("awc-satellite-image")) {
        showFrame(state.index, { force: true });
      }
    });
    window.addEventListener("awc:forecast-start", (event) => {
      applyLocation(event.detail?.location);
      enableViewer();
    });
  }

  function startRefreshTimer() {
    if (state.refreshTimer !== null) return;
    state.refreshTimer = window.setInterval(refreshMetadata, METADATA_REFRESH_MS);
  }

  function enableViewer() {
    state.enabled = true;
    const viewer = byId("awc-satellite-viewer");
    if (!viewer) {
      queueMount();
      return;
    }
    viewer.dataset.enabled = "true";
    showPlaceholder("Echte EUMETSAT-Aufnahmen werden vorbereitet …", { removeImage: false });
    setStatus("EUMETSAT-Aufnahmen werden abgefragt …", "LOADING");
    updateControlAvailability();
    startRefreshTimer();
    loadMetadata(state.product, { keepLatest: true, force: true });
  }

  async function loadMetadata(product, options = {}) {
    if (!state.enabled || state.loadingMetadata) return;
    state.loadingMetadata = true;
    setStatus("EUMETSAT-Aufnahmen werden abgefragt …", "LOADING");
    try {
      const response = await fetch(`${API}/meta?product=${encodeURIComponent(product)}`, {
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const metadata = await response.json();
      if (!state.enabled || !byId("awc-satellite-viewer")) return;
      state.metadata = metadata;
      state.location ||= metadata.location || null;
      populateProducts(metadata.products || []);
      state.frames = Array.isArray(metadata.frames) ? metadata.frames : [];
      const range = byId("awc-satellite-range");
      if (range) range.max = String(Math.max(0, state.frames.length - 1));
      updateControlAvailability();
      if (!state.frames.length) {
        state.index = -1;
        showPlaceholder("Für dieses Produkt sind derzeit keine echten Frames verfügbar.");
        setStatus("Keine EUMETSAT-Aufnahme verfügbar", "ERROR");
        return;
      }
      const target = options.keepLatest
        ? state.frames.length - 1
        : Math.max(0, Math.min(state.frames.length - 1, state.index));
      await showFrame(target, { force: options.force });
    } catch (error) {
      console.warn("EUMETSAT-Metadaten konnten nicht geladen werden", error);
      if (state.enabled && byId("awc-satellite-viewer")) {
        showPlaceholder("EUMETSAT antwortet derzeit nicht. Es wird kein Ersatz- oder Fake-Bild angezeigt.");
        setStatus("Satellitendienst nicht erreichbar", "ERROR");
      }
    } finally {
      state.loadingMetadata = false;
      updateControlAvailability();
    }
  }

  async function refreshMetadata() {
    if (!state.enabled || document.hidden || state.playing) return;
    if (!byId("awc-satellite-viewer")) {
      queueMount();
      return;
    }
    const wasLatest = state.index === state.frames.length - 1;
    await loadMetadata(state.product, { keepLatest: wasLatest, force: wasLatest });
  }

  function populateProducts(products) {
    const select = byId("awc-satellite-product");
    if (!select) return;
    const signature = JSON.stringify(products.map((product) => [
      product.key,
      product.title,
      product.available,
      product.latestTime,
    ]));
    if (select.dataset.signature !== signature) {
      select.replaceChildren(...products.map((product) => {
        const option = document.createElement("option");
        option.value = product.key;
        const mode = productMode(product.key);
        option.textContent = product.available
          ? `${product.title}${mode ? ` · ${mode}` : ""}`
          : `${product.title} · nicht verfügbar`;
        option.disabled = !product.available;
        option.title = product.description || "";
        return option;
      }));
      select.dataset.signature = signature;
    }
    if ([...select.options].some((option) => option.value === state.product && !option.disabled)) {
      select.value = state.product;
      return;
    }
    const firstColour = products.find((product) => product.available && product.key === "cloudtype")
      || products.find((product) => product.available && product.key !== "infrared")
      || products.find((product) => product.available);
    if (firstColour) {
      state.product = firstColour.key;
      select.value = firstColour.key;
    }
  }

  function latestStatusFromHeaders(response) {
    const observedAt = response.headers.get("X-Satellite-Observation-Time");
    const ageMinutes = Number(response.headers.get("X-Satellite-Age-Minutes"));
    const fresh = response.headers.get("X-Satellite-Fresh");
    const actualProduct = response.headers.get("X-Satellite-Product") || state.product;
    const switched = actualProduct !== state.product;
    const productCopy = switched
      ? `AUTO aktuell: ${productTitle(state.product)} → ${productTitle(actualProduct)}`
      : productTitle(actualProduct);
    if (observedAt && Number.isFinite(ageMinutes)) {
      const roundedAge = Math.max(0, Math.round(ageMinutes));
      const formatted = formatTime(observedAt);
      setText(byId("awc-satellite-time"), `LIVE · ${formatted} · ${roundedAge} Min. alt`);
      const prefix = `${productCopy} · ${formatted}`;
      if (fresh === "true") {
        setStatus(`LIVE · ${prefix} · ${roundedAge} Min. alt`, "FRESH");
      } else {
        setStatus(
          `VERALTET · ${productCopy} · ${roundedAge} Min. alt · Grenze ${Math.round(freshnessLimitMinutes())} Min.`,
          "STALE",
        );
      }
      return;
    }
    setText(byId("awc-satellite-time"), "LIVE · Aufnahmezeit nicht verifiziert");
    setStatus(`${productCopy} · Aufnahmezeit nicht verifiziert`, "WAITING");
  }

  function imageLoadFailed(image, placeholder) {
    if (!image.isConnected) return;
    image.classList.remove("is-loading");
    setHidden(placeholder, false);
    setText(placeholder, "Diese echte EUMETSAT-Aufnahme konnte nicht geladen werden.");
    setStatus("Aufnahme nicht verfügbar", "ERROR");
  }

  async function loadLatestFrame(url, image, placeholder, serial) {
    hideHistoricalOverlay();
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      if (serial !== state.loadSerial || !image.isConnected) return false;
      const objectUrl = URL.createObjectURL(blob);
      clearImageObjectUrl();
      state.imageObjectUrl = objectUrl;
      return await new Promise((resolve) => {
        image.onload = () => {
          if (serial !== state.loadSerial || !image.isConnected) return resolve(false);
          setHidden(placeholder, true);
          image.classList.remove("is-loading");
          latestStatusFromHeaders(response);
          schedulePrefetch();
          resolve(true);
        };
        image.onerror = () => {
          if (serial === state.loadSerial) imageLoadFailed(image, placeholder);
          resolve(false);
        };
        image.src = objectUrl;
      });
    } catch (error) {
      console.warn("EUMETSAT-Livebild konnte nicht geladen werden", error);
      if (serial === state.loadSerial) imageLoadFailed(image, placeholder);
      return false;
    }
  }

  async function loadHistoricalFrame(frame, image, placeholder, serial, options = {}) {
    clearImageObjectUrl();
    const playback = options.playback === true;
    const url = historicalWmsUrl(frame, { hd: !playback });
    showHistoricalOverlay(frame, playback);
    return await new Promise((resolve) => {
      image.onload = () => {
        if (serial !== state.loadSerial || !image.isConnected) return resolve(false);
        setHidden(placeholder, true);
        image.classList.remove("is-loading");
        updateFrameStatus(frame, playback);
        resolve(true);
      };
      image.onerror = () => {
        if (serial === state.loadSerial) imageLoadFailed(image, placeholder);
        resolve(false);
      };
      if (image.getAttribute("src") !== url || options.force) {
        image.src = url;
      } else if (image.complete && image.naturalWidth > 0) {
        image.onload();
      }
    });
  }

  async function showFrame(index, options = {}) {
    if (!state.enabled || !state.frames.length) return false;
    const bounded = Math.max(0, Math.min(state.frames.length - 1, index));
    if (bounded === state.index && !options.force) return true;
    state.index = bounded;
    const frame = state.frames[bounded];
    const latest = bounded === state.frames.length - 1;
    const image = byId("awc-satellite-image");
    const placeholder = byId("awc-satellite-placeholder");
    const range = byId("awc-satellite-range");
    if (!image || !placeholder) return false;

    state.loadSerial += 1;
    const serial = state.loadSerial;
    if (range && range.value !== String(bounded)) range.value = String(bounded);
    setHidden(placeholder, false);
    setText(
      placeholder,
      latest
        ? "Neueste EUMETSAT-Aufnahme wird geprüft und in HD geladen …"
        : options.playback
          ? "Filmframe wird geladen …"
          : "Historisches Satellitenbild wird in HD geladen …",
    );
    image.classList.add("is-loading");

    let loaded;
    if (latest) {
      setText(byId("awc-satellite-time"), "LIVE · Aufnahmezeit wird geprüft …");
      loaded = await loadLatestFrame(localImageUrl(frame, true), image, placeholder, serial);
    } else {
      setText(byId("awc-satellite-time"), formatTime(frame));
      loaded = await loadHistoricalFrame(frame, image, placeholder, serial, options);
    }
    updateControlAvailability();
    return loaded;
  }

  function updateFrameStatus(frame, playback = false) {
    setStatus(
      `${playback ? "Film" : "Historische Aufnahme"} · ${formatTime(frame)} · ${productTitle(state.product)}`,
      "HISTORY",
    );
  }

  function locationPercent() {
    if (!state.location) return null;
    const minimumLongitude = 8.75;
    const minimumLatitude = 47.0;
    const maximumLongitude = 14.05;
    const maximumLatitude = 50.75;
    const x = (state.location.longitude - minimumLongitude)
      / (maximumLongitude - minimumLongitude) * 100;
    const y = (maximumLatitude - state.location.latitude)
      / (maximumLatitude - minimumLatitude) * 100;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x, y };
  }

  function showHistoricalOverlay(frame, playback = false) {
    const layer = byId("awc-satellite-pin-layer");
    const pin = byId("awc-satellite-pin");
    const label = byId("awc-satellite-pin-label");
    const provenance = byId("awc-satellite-provenance");
    const position = locationPercent();
    if (layer && pin && label && position) {
      pin.style.left = `${position.x}%`;
      pin.style.top = `${position.y}%`;
      label.style.left = `${position.x}%`;
      label.style.top = `${position.y}%`;
      setText(label, state.location?.name || "Ausgewählter Ort");
      setHidden(layer, false);
    }
    if (provenance) {
      setText(
        provenance,
        `EUMETSAT Meteosat-12 / MTG-FCI · ${productTitle(state.product)} · ${formatTime(frame)}${playback ? " · Vorschau" : " · HD"}`,
      );
      setHidden(provenance, false);
    }
  }

  function updateHistoricalOverlay() {
    if (state.index < 0 || state.index >= state.frames.length - 1) return;
    showHistoricalOverlay(state.frames[state.index], state.playing);
  }

  function hideHistoricalOverlay() {
    setHidden(byId("awc-satellite-pin-layer"), true);
    setHidden(byId("awc-satellite-provenance"), true);
  }

  function setStatus(copy, tone) {
    const node = byId("awc-satellite-status");
    if (!node) return;
    setText(node, copy);
    if (node.dataset.tone !== tone) node.dataset.tone = tone;
  }

  function showPlaceholder(copy, options = {}) {
    const image = byId("awc-satellite-image");
    const placeholder = byId("awc-satellite-placeholder");
    if (image && options.removeImage !== false && image.hasAttribute("src")) {
      state.loadSerial += 1;
      clearImageObjectUrl();
      image.removeAttribute("src");
    }
    if (placeholder) {
      setHidden(placeholder, false);
      setText(placeholder, copy);
    }
  }

  function updateControlAvailability() {
    const viewer = byId("awc-satellite-viewer");
    if (viewer) {
      viewer.dataset.enabled = String(state.enabled);
      viewer.dataset.playing = String(state.playing);
    }
    const hasFrames = state.enabled && state.frames.length > 0;
    const hasMultiple = hasFrames && state.frames.length > 1;
    setDisabled(byId("awc-satellite-product"), !state.enabled || state.loadingMetadata || state.playing);
    setDisabled(byId("awc-satellite-refresh"), !state.enabled || state.loadingMetadata || state.playing);
    setDisabled(byId("awc-satellite-fullscreen"), !state.enabled);
    setDisabled(byId("awc-satellite-prev"), !hasFrames || state.index <= 0 || state.playing);
    setDisabled(byId("awc-satellite-next"), !hasFrames || state.index >= state.frames.length - 1 || state.playing);
    setDisabled(byId("awc-satellite-play"), !hasMultiple);
    setDisabled(byId("awc-satellite-range"), !hasMultiple || state.playing);
    setDisabled(byId("awc-satellite-zoom-out"), !hasFrames || state.playing);
    setDisabled(byId("awc-satellite-zoom-reset"), !hasFrames || state.playing);
    setDisabled(byId("awc-satellite-zoom-in"), !hasFrames || state.playing);
  }

  function previewKey(frame) {
    return `${state.product}|${frame}`;
  }

  function ensurePreview(frame) {
    const key = previewKey(frame);
    const existing = state.previewPromises.get(key);
    if (existing) return existing;
    const url = historicalWmsUrl(frame, { hd: false });
    const promise = new Promise((resolve) => {
      const preload = new Image();
      preload.onload = () => resolve(true);
      preload.onerror = () => resolve(false);
      preload.src = url;
      if (preload.complete && preload.naturalWidth > 0) resolve(true);
    });
    state.previewPromises.set(key, promise);
    return promise;
  }

  function schedulePrefetch() {
    if (!state.enabled || state.frames.length < 2) return;
    const generation = ++state.prefetchGeneration;
    const frames = state.frames.slice(0, -1);
    let cursor = 0;
    const worker = async () => {
      while (generation === state.prefetchGeneration && cursor < frames.length) {
        const frame = frames[cursor++];
        await ensurePreview(frame);
      }
    };
    for (let index = 0; index < Math.min(PREFETCH_CONCURRENCY, frames.length); index += 1) {
      worker();
    }
  }

  function delay(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  async function togglePlayback() {
    if (!state.enabled) return;
    if (state.playing) {
      stopPlayback();
      return;
    }
    if (state.frames.length < 2) return;
    state.playing = true;
    state.playbackGeneration += 1;
    const generation = state.playbackGeneration;
    resetTransform();
    setText(byId("awc-satellite-play"), "Ⅱ Pause");
    updateControlAvailability();

    let start = state.index;
    if (start >= state.frames.length - 1 || start < 0) start = 0;
    await ensurePreview(state.frames[start]);
    if (!state.playing || generation !== state.playbackGeneration) return;
    await showFrame(start, { force: true, playback: true });

    for (let next = start + 1; next < state.frames.length - 1; next += 1) {
      if (!state.playing || generation !== state.playbackGeneration) return;
      const frame = state.frames[next];
      await ensurePreview(frame);
      if (!state.playing || generation !== state.playbackGeneration) return;
      const shownAt = performance.now();
      await showFrame(next, { force: true, playback: true });
      const remaining = FRAME_DURATION_MS - (performance.now() - shownAt);
      if (remaining > 0) await delay(remaining);
    }
    stopPlayback();
  }

  function stopPlayback(options = {}) {
    const wasPlaying = state.playing;
    state.playing = false;
    state.playbackGeneration += 1;
    setText(byId("awc-satellite-play"), "▶ Abspielen");
    updateControlAvailability();
    if (
      wasPlaying
      && options.reloadHd !== false
      && state.index >= 0
      && state.index < state.frames.length - 1
    ) {
      showFrame(state.index, { force: true });
    }
  }

  function zoomBy(delta, originX = 0, originY = 0) {
    if (!state.enabled || !state.frames.length) return;
    const previous = state.scale;
    const next = Math.max(1, Math.min(8, previous + delta));
    if (next === previous) return;
    const ratio = next / previous;
    state.translateX = originX - (originX - state.translateX) * ratio;
    state.translateY = originY - (originY - state.translateY) * ratio;
    state.scale = next;
    constrainTransform();
    applyTransform();
  }

  function onWheel(event) {
    if (!state.enabled || !state.frames.length || state.playing) return;
    event.preventDefault();
    const viewport = byId("awc-satellite-viewport");
    if (!viewport) return;
    const rect = viewport.getBoundingClientRect();
    zoomBy(
      event.deltaY < 0 ? 0.35 : -0.35,
      event.clientX - rect.left,
      event.clientY - rect.top,
    );
  }

  function onPointerDown(event) {
    if (!state.enabled || state.scale <= 1 || state.playing) return;
    const viewport = event.currentTarget;
    state.pointerId = event.pointerId;
    state.pointerX = event.clientX;
    state.pointerY = event.clientY;
    viewport.setPointerCapture(event.pointerId);
    viewport.classList.add("is-dragging");
  }

  function onPointerMove(event) {
    if (state.pointerId !== event.pointerId) return;
    state.translateX += event.clientX - state.pointerX;
    state.translateY += event.clientY - state.pointerY;
    state.pointerX = event.clientX;
    state.pointerY = event.clientY;
    constrainTransform();
    applyTransform();
  }

  function onPointerUp(event) {
    if (state.pointerId !== event.pointerId) return;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    event.currentTarget.classList.remove("is-dragging");
    state.pointerId = null;
  }

  function constrainTransform() {
    const viewport = byId("awc-satellite-viewport");
    if (!viewport || state.scale <= 1) {
      state.translateX = 0;
      state.translateY = 0;
      return;
    }
    const maxX = viewport.clientWidth * (state.scale - 1) / 2;
    const maxY = viewport.clientHeight * (state.scale - 1) / 2;
    state.translateX = Math.max(-maxX, Math.min(maxX, state.translateX));
    state.translateY = Math.max(-maxY, Math.min(maxY, state.translateY));
  }

  function applyTransform() {
    const image = byId("awc-satellite-image");
    const pinLayer = byId("awc-satellite-pin-layer");
    const reset = byId("awc-satellite-zoom-reset");
    const transform = `translate(${state.translateX}px, ${state.translateY}px) scale(${state.scale})`;
    if (image && image.style.transform !== transform) image.style.transform = transform;
    if (pinLayer && pinLayer.style.transform !== transform) pinLayer.style.transform = transform;
    setText(reset, `${Math.round(state.scale * 100)} %`);
  }

  function resetTransform() {
    state.scale = 1;
    state.translateX = 0;
    state.translateY = 0;
    applyTransform();
  }

  async function toggleFullscreen() {
    if (!state.enabled) return;
    const viewer = byId("awc-satellite-viewer");
    if (!viewer) return;
    try {
      if (document.fullscreenElement === viewer) {
        await document.exitFullscreen();
      } else {
        await viewer.requestFullscreen();
      }
    } catch (error) {
      console.warn("Vollbildmodus konnte nicht geöffnet werden", error);
      setStatus("Vollbildmodus wird von diesem Browser blockiert", "ERROR");
    }
  }

  function updateFullscreenButton() {
    setText(
      byId("awc-satellite-fullscreen"),
      document.fullscreenElement ? "Vollbild schließen" : "Vollbild",
    );
    resetTransform();
  }

  function boot() {
    bindGlobalEvents();
    queueMount();
  }

  window.AstroWolkencheckSatelliteViewer = Object.freeze({
    mount: queueMount,
    start: enableViewer,
    refresh: () => {
      if (state.enabled) {
        state.previewPromises.clear();
        loadMetadata(state.product, { keepLatest: true, force: true });
      }
    },
    getState: () => ({
      enabled: state.enabled,
      product: state.product,
      frameCount: state.frames.length,
      index: state.index,
      playing: state.playing,
      scale: state.scale,
      location: state.location ? { ...state.location } : null,
    }),
  });

  boot();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => queueMount(), { once: true });
  }
})();
