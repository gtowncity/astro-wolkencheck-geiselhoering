(() => {
  "use strict";

  const API = "/api/v1/satellite";
  const METADATA_REFRESH_MS = 120000;
  const FRAME_DURATION_MS = 900;
  const MAX_MOUNT_ATTEMPTS = 40;
  const state = {
    enabled: false,
    product: "geocolour",
    metadata: null,
    frames: [],
    index: -1,
    playing: false,
    playTimer: null,
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

  function formatTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "unbekannte Zeit";
    return new Intl.DateTimeFormat("de-DE", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "UTC",
      timeZoneName: "short",
    }).format(date);
  }

  function frameAgeMinutes(value) {
    const timestamp = new Date(value).getTime();
    if (!Number.isFinite(timestamp)) return null;
    return Math.max(0, (Date.now() - timestamp) / 60000);
  }

  function imageUrl(frame) {
    const params = new URLSearchParams({
      product: state.product,
      time: frame,
    });
    if (state.location) {
      params.set("latitude", String(state.location.latitude));
      params.set("longitude", String(state.location.longitude));
      params.set("location_name", state.location.name);
    }
    return `${API}/image?${params.toString()}`;
  }

  function viewerMarkup() {
    return `
      <section id="awc-satellite-viewer" class="awc-satellite-viewer" data-enabled="false" aria-labelledby="awc-satellite-title">
        <header class="awc-satellite-toolbar">
          <div>
            <span class="awc-satellite-kicker">ECHTES SATELLITENBILD</span>
            <strong id="awc-satellite-title">EUMETSAT Meteosat-12</strong>
          </div>
          <div class="awc-satellite-actions">
            <label>
              <span>Produkt</span>
              <select id="awc-satellite-product" aria-label="Satellitenprodukt auswählen" disabled>
                <option value="geocolour">Nach Forecast-Start verfügbar</option>
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
          <img id="awc-satellite-image" alt="Echtes EUMETSAT-Satellitenbild von Bayern mit rotem Orts-Pin" draggable="false">
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
        <p class="awc-satellite-note">Quelle nach bewusstem Start: EUMETSAT EUMETView · MTG-FCI · Bayern-Ausschnitt. Historische Frames sind bewusst älter; nur die neueste Aufnahme wird gegen die 20-Minuten-Aktualitätsgrenze geprüft.</p>
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

    stopPlayback();
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
      stopPlayback();
      state.product = event.target.value;
      resetTransform();
      loadMetadata(state.product, { keepLatest: true });
    });
    byId("awc-satellite-refresh")?.addEventListener("click", () => {
      if (!state.enabled) return;
      loadMetadata(state.product, { keepLatest: true, force: true });
    });
    byId("awc-satellite-fullscreen")?.addEventListener("click", toggleFullscreen);
    byId("awc-satellite-play")?.addEventListener("click", togglePlayback);
    byId("awc-satellite-prev")?.addEventListener("click", () => showFrame(state.index - 1));
    byId("awc-satellite-next")?.addEventListener("click", () => showFrame(state.index + 1));
    byId("awc-satellite-range")?.addEventListener("input", (event) => {
      stopPlayback();
      showFrame(Number(event.target.value));
    });
    byId("awc-satellite-zoom-in")?.addEventListener("click", () => zoomBy(0.35));
    byId("awc-satellite-zoom-out")?.addEventListener("click", () => zoomBy(-0.35));
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
    return detail
      && Number.isFinite(detail.latitude)
      && Number.isFinite(detail.longitude);
  }

  function applyLocation(detail) {
    if (!validLocation(detail)) return false;
    state.location = {
      name: String(detail.name || "Ausgewählter Ort"),
      latitude: detail.latitude,
      longitude: detail.longitude,
    };
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
    showPlaceholder("Echte EUMETSAT-Aufnahmen werden vorbereitet …", {
      removeImage: false,
    });
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
      showFrame(target, { force: options.force });
    } catch (error) {
      console.warn("EUMETSAT-Metadaten konnten nicht geladen werden", error);
      if (state.enabled && byId("awc-satellite-viewer")) {
        showPlaceholder(
          "EUMETSAT antwortet derzeit nicht. Es wird kein Ersatz- oder Fake-Bild angezeigt."
        );
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
    const previousLatest = state.frames.at(-1);
    const wasLatest = state.index === state.frames.length - 1;
    await loadMetadata(state.product, { keepLatest: wasLatest });
    const nextLatest = state.frames.at(-1);
    if (previousLatest && nextLatest && previousLatest !== nextLatest) {
      setStatus("Neue EUMETSAT-Aufnahme verfügbar", "FRESH");
    }
  }

  function populateProducts(products) {
    const select = byId("awc-satellite-product");
    if (!select) return;
    const signature = JSON.stringify(
      products.map((product) => [
        product.key,
        product.title,
        product.available,
        product.fresh,
      ]),
    );
    if (select.dataset.signature !== signature) {
      select.replaceChildren(...products.map((product) => {
        const option = document.createElement("option");
        option.value = product.key;
        option.textContent = product.available
          ? `${product.title}${product.fresh ? " · aktuell" : " · veraltet"}`
          : `${product.title} · nicht verfügbar`;
        option.disabled = !product.available;
        option.title = product.description || "";
        return option;
      }));
      select.dataset.signature = signature;
    }
    if ([...select.options].some((option) => option.value === state.product && !option.disabled)) {
      select.value = state.product;
    }
  }

  function showFrame(index, options = {}) {
    if (!state.enabled || !state.frames.length) return;
    const bounded = Math.max(0, Math.min(state.frames.length - 1, index));
    if (bounded === state.index && !options.force) return;
    state.index = bounded;
    const frame = state.frames[bounded];
    const image = byId("awc-satellite-image");
    const placeholder = byId("awc-satellite-placeholder");
    const range = byId("awc-satellite-range");
    if (!image || !placeholder) return;

    if (range && range.value !== String(bounded)) range.value = String(bounded);
    setHidden(placeholder, false);
    setText(placeholder, "Echtes Satellitenbild wird geladen …");
    image.classList.add("is-loading");
    const url = imageUrl(frame);
    image.onload = () => {
      if (!image.isConnected || image.src !== new URL(url, window.location.href).href) return;
      setHidden(placeholder, true);
      image.classList.remove("is-loading");
      updateFrameStatus(frame);
    };
    image.onerror = () => {
      if (!image.isConnected) return;
      image.classList.remove("is-loading");
      setHidden(placeholder, false);
      setText(placeholder, "Dieser echte EUMETSAT-Frame konnte nicht geladen werden.");
      setStatus("Aufnahme nicht verfügbar", "ERROR");
    };
    if (image.getAttribute("src") !== url || options.force) image.src = url;
    setText(byId("awc-satellite-time"), formatTime(frame));
    updateControlAvailability();
  }

  function updateFrameStatus(frame) {
    const age = frameAgeMinutes(frame);
    const latest = state.index === state.frames.length - 1;
    if (!latest) {
      setStatus(`Historische Aufnahme · ${formatTime(frame)}`, "HISTORY");
      return;
    }
    const limit = Number(state.metadata?.freshnessLimitMinutes || 20);
    if (age !== null && age <= limit) {
      setStatus(`Aktuell · ${Math.round(age)} Min. alt · 10-Minuten-MTG-Zyklus`, "FRESH");
    } else if (age !== null) {
      setStatus(`VERALTET · ${Math.round(age)} Min. alt · Grenze ${limit} Min.`, "STALE");
    } else {
      setStatus("Aufnahmezeit unbekannt", "ERROR");
    }
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
      image.removeAttribute("src");
    }
    if (placeholder) {
      setHidden(placeholder, false);
      setText(placeholder, copy);
    }
  }

  function updateControlAvailability() {
    const viewer = byId("awc-satellite-viewer");
    if (viewer) viewer.dataset.enabled = String(state.enabled);
    const hasFrames = state.enabled && state.frames.length > 0;
    const hasMultiple = hasFrames && state.frames.length > 1;
    setDisabled(byId("awc-satellite-product"), !state.enabled || state.loadingMetadata);
    setDisabled(byId("awc-satellite-refresh"), !state.enabled || state.loadingMetadata);
    setDisabled(byId("awc-satellite-fullscreen"), !state.enabled);
    setDisabled(byId("awc-satellite-prev"), !hasFrames || state.index <= 0);
    setDisabled(
      byId("awc-satellite-next"),
      !hasFrames || state.index >= state.frames.length - 1,
    );
    setDisabled(byId("awc-satellite-play"), !hasMultiple);
    setDisabled(byId("awc-satellite-range"), !hasMultiple);
    setDisabled(byId("awc-satellite-zoom-out"), !hasFrames);
    setDisabled(byId("awc-satellite-zoom-reset"), !hasFrames);
    setDisabled(byId("awc-satellite-zoom-in"), !hasFrames);
  }

  function togglePlayback() {
    if (!state.enabled) return;
    if (state.playing) {
      stopPlayback();
      return;
    }
    if (state.frames.length < 2) return;
    if (state.index >= state.frames.length - 1) showFrame(0, { force: true });
    state.playing = true;
    setText(byId("awc-satellite-play"), "Ⅱ Pause");
    state.playTimer = window.setInterval(() => {
      const next = state.index + 1;
      if (next >= state.frames.length) {
        stopPlayback();
        return;
      }
      showFrame(next);
    }, FRAME_DURATION_MS);
  }

  function stopPlayback() {
    state.playing = false;
    if (state.playTimer !== null) window.clearInterval(state.playTimer);
    state.playTimer = null;
    setText(byId("awc-satellite-play"), "▶ Abspielen");
  }

  function zoomBy(delta, originX = 0, originY = 0) {
    if (!state.enabled || !state.frames.length) return;
    const previous = state.scale;
    const next = Math.max(1, Math.min(6, previous + delta));
    if (next === previous) return;
    const ratio = next / previous;
    state.translateX = originX - (originX - state.translateX) * ratio;
    state.translateY = originY - (originY - state.translateY) * ratio;
    state.scale = next;
    constrainTransform();
    applyTransform();
  }

  function onWheel(event) {
    if (!state.enabled || !state.frames.length) return;
    event.preventDefault();
    const viewport = byId("awc-satellite-viewport");
    if (!viewport) return;
    const rect = viewport.getBoundingClientRect();
    zoomBy(
      event.deltaY < 0 ? 0.3 : -0.3,
      event.clientX - rect.left,
      event.clientY - rect.top,
    );
  }

  function onPointerDown(event) {
    if (!state.enabled || state.scale <= 1) return;
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
    const reset = byId("awc-satellite-zoom-reset");
    const transform = `translate(${state.translateX}px, ${state.translateY}px) scale(${state.scale})`;
    if (image && image.style.transform !== transform) image.style.transform = transform;
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
