(() => {
  "use strict";

  const API = "/api/v1/satellite";
  const METADATA_REFRESH_MS = 120000;
  const FRAME_DURATION_MS = 900;
  const state = {
    product: "geocolour",
    metadata: null,
    frames: [],
    index: -1,
    playing: false,
    playTimer: null,
    scale: 1,
    translateX: 0,
    translateY: 0,
    pointerId: null,
    pointerX: 0,
    pointerY: 0,
    location: null,
    mounted: false,
    loadingMetadata: false,
  };

  const byId = (id) => document.getElementById(id);

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

  function mount() {
    const host = document.querySelector(".awc-radar-visual");
    if (!host) {
      window.setTimeout(mount, 120);
      return;
    }
    if (state.mounted && byId("awc-satellite-viewer")) return;

    host.classList.add("awc-satellite-host");
    host.removeAttribute("role");
    host.removeAttribute("aria-label");
    host.innerHTML = `
      <section id="awc-satellite-viewer" class="awc-satellite-viewer" aria-labelledby="awc-satellite-title">
        <header class="awc-satellite-toolbar">
          <div>
            <span class="awc-satellite-kicker">ECHTES SATELLITENBILD</span>
            <strong id="awc-satellite-title">EUMETSAT Meteosat-12</strong>
          </div>
          <div class="awc-satellite-actions">
            <label>
              <span>Produkt</span>
              <select id="awc-satellite-product" aria-label="Satellitenprodukt auswählen"></select>
            </label>
            <button id="awc-satellite-refresh" type="button" title="Aufnahmen neu laden">↻</button>
            <button id="awc-satellite-fullscreen" type="button">Vollbild</button>
          </div>
        </header>
        <div id="awc-satellite-status" class="awc-satellite-status" data-tone="LOADING">
          Verfügbare EUMETSAT-Aufnahmen werden geladen …
        </div>
        <div id="awc-satellite-viewport" class="awc-satellite-viewport" tabindex="0" aria-label="Satellitenbild. Mit Mausrad oder Plus und Minus zoomen; im Zoom ziehen.">
          <img id="awc-satellite-image" alt="Echtes EUMETSAT-Satellitenbild von Bayern mit rotem Orts-Pin" draggable="false">
          <div id="awc-satellite-placeholder" class="awc-satellite-placeholder">Satellitenbild wird vorbereitet …</div>
          <div class="awc-satellite-zoom" aria-label="Zoomsteuerung">
            <button id="awc-satellite-zoom-out" type="button" aria-label="Herauszoomen">−</button>
            <button id="awc-satellite-zoom-reset" type="button" aria-label="Zoom zurücksetzen">100 %</button>
            <button id="awc-satellite-zoom-in" type="button" aria-label="Hineinzoomen">+</button>
          </div>
        </div>
        <footer class="awc-satellite-timeline">
          <button id="awc-satellite-prev" type="button" aria-label="Vorherige Aufnahme">◀</button>
          <button id="awc-satellite-play" type="button" aria-label="Zeitverlauf abspielen">▶ Abspielen</button>
          <input id="awc-satellite-range" type="range" min="0" max="0" value="0" step="1" aria-label="Aufnahmezeit auswählen">
          <button id="awc-satellite-next" type="button" aria-label="Nächste Aufnahme">▶</button>
          <strong id="awc-satellite-time">keine Aufnahme</strong>
        </footer>
        <p class="awc-satellite-note">Quelle: EUMETSAT EUMETView · MTG-FCI · Bayern-Ausschnitt. Historische Frames sind bewusst älter; nur die neueste Aufnahme wird gegen die 20-Minuten-Aktualitätsgrenze geprüft.</p>
      </section>
    `;
    state.mounted = true;
    bindEvents();
    loadMetadata(state.product, { keepLatest: true });
    window.setInterval(refreshMetadata, METADATA_REFRESH_MS);
  }

  function bindEvents() {
    byId("awc-satellite-product")?.addEventListener("change", (event) => {
      stopPlayback();
      state.product = event.target.value;
      resetTransform();
      loadMetadata(state.product, { keepLatest: true });
    });
    byId("awc-satellite-refresh")?.addEventListener("click", () => {
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
    document.addEventListener("fullscreenchange", updateFullscreenButton);
    window.addEventListener("awc:forecast-location", (event) => {
      const detail = event.detail;
      if (!detail || !Number.isFinite(detail.latitude) || !Number.isFinite(detail.longitude)) {
        return;
      }
      state.location = {
        name: String(detail.name || "Ausgewählter Ort"),
        latitude: detail.latitude,
        longitude: detail.longitude,
      };
      if (state.index >= 0) showFrame(state.index, { force: true });
    });
  }

  async function loadMetadata(product, options = {}) {
    if (state.loadingMetadata) return;
    state.loadingMetadata = true;
    setStatus("EUMETSAT-Aufnahmen werden abgefragt …", "LOADING");
    try {
      const response = await fetch(`${API}/meta?product=${encodeURIComponent(product)}`, {
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const metadata = await response.json();
      state.metadata = metadata;
      state.location ||= metadata.location || null;
      populateProducts(metadata.products || []);
      state.frames = Array.isArray(metadata.frames) ? metadata.frames : [];
      const range = byId("awc-satellite-range");
      if (range) {
        range.max = String(Math.max(0, state.frames.length - 1));
        range.disabled = state.frames.length < 2;
      }
      updateControlAvailability();
      if (!state.frames.length) {
        state.index = -1;
        showPlaceholder("Für dieses Produkt sind derzeit keine echten Frames verfügbar.");
        setStatus("Keine EUMETSAT-Aufnahme verfügbar", "ERROR");
        return;
      }
      const target = options.keepLatest ? state.frames.length - 1 : Math.max(0, state.index);
      showFrame(target, { force: options.force });
    } catch (error) {
      console.warn("EUMETSAT-Metadaten konnten nicht geladen werden", error);
      showPlaceholder("EUMETSAT antwortet derzeit nicht. Es wird kein Ersatz- oder Fake-Bild angezeigt.");
      setStatus("Satellitendienst nicht erreichbar", "ERROR");
    } finally {
      state.loadingMetadata = false;
    }
  }

  async function refreshMetadata() {
    if (!state.mounted || document.hidden || state.playing) return;
    const previousLatest = state.frames.at(-1);
    await loadMetadata(state.product, { keepLatest: state.index === state.frames.length - 1 });
    const nextLatest = state.frames.at(-1);
    if (previousLatest && nextLatest && previousLatest !== nextLatest) {
      setStatus("Neue EUMETSAT-Aufnahme verfügbar", "FRESH");
    }
  }

  function populateProducts(products) {
    const select = byId("awc-satellite-product");
    if (!select) return;
    const current = state.product;
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
    if ([...select.options].some((option) => option.value === current && !option.disabled)) {
      select.value = current;
    }
  }

  function showFrame(index, options = {}) {
    if (!state.frames.length) return;
    const bounded = Math.max(0, Math.min(state.frames.length - 1, index));
    if (bounded === state.index && !options.force) return;
    state.index = bounded;
    const frame = state.frames[bounded];
    const image = byId("awc-satellite-image");
    const placeholder = byId("awc-satellite-placeholder");
    const range = byId("awc-satellite-range");
    if (!image || !placeholder) return;

    if (range) range.value = String(bounded);
    placeholder.hidden = false;
    placeholder.textContent = "Echtes Satellitenbild wird geladen …";
    image.classList.add("is-loading");
    const url = imageUrl(frame);
    image.onload = () => {
      placeholder.hidden = true;
      image.classList.remove("is-loading");
      updateFrameStatus(frame);
      preloadFrame(bounded + 1);
    };
    image.onerror = () => {
      image.classList.remove("is-loading");
      placeholder.hidden = false;
      placeholder.textContent = "Dieser echte EUMETSAT-Frame konnte nicht geladen werden.";
      setStatus("Aufnahme nicht verfügbar", "ERROR");
    };
    image.src = url;
    byId("awc-satellite-time").textContent = formatTime(frame);
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
    node.textContent = copy;
    node.dataset.tone = tone;
  }

  function showPlaceholder(copy) {
    const image = byId("awc-satellite-image");
    const placeholder = byId("awc-satellite-placeholder");
    if (image) image.removeAttribute("src");
    if (placeholder) {
      placeholder.hidden = false;
      placeholder.textContent = copy;
    }
  }

  function updateControlAvailability() {
    const hasFrames = state.frames.length > 0;
    const prev = byId("awc-satellite-prev");
    const next = byId("awc-satellite-next");
    const play = byId("awc-satellite-play");
    if (prev) prev.disabled = !hasFrames || state.index <= 0;
    if (next) next.disabled = !hasFrames || state.index >= state.frames.length - 1;
    if (play) play.disabled = state.frames.length < 2;
  }

  function togglePlayback() {
    if (state.playing) {
      stopPlayback();
      return;
    }
    if (state.frames.length < 2) return;
    if (state.index >= state.frames.length - 1) showFrame(0, { force: true });
    state.playing = true;
    const button = byId("awc-satellite-play");
    if (button) button.textContent = "Ⅱ Pause";
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
    const button = byId("awc-satellite-play");
    if (button) button.textContent = "▶ Abspielen";
  }

  function preloadFrame(index) {
    if (index < 0 || index >= state.frames.length) return;
    const preload = new Image();
    preload.src = imageUrl(state.frames[index]);
  }

  function zoomBy(delta, originX = 0, originY = 0) {
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
    event.preventDefault();
    const viewport = byId("awc-satellite-viewport");
    if (!viewport) return;
    const rect = viewport.getBoundingClientRect();
    zoomBy(event.deltaY < 0 ? 0.3 : -0.3, event.clientX - rect.left, event.clientY - rect.top);
  }

  function onPointerDown(event) {
    if (state.scale <= 1) return;
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
    if (image) {
      image.style.transform = `translate(${state.translateX}px, ${state.translateY}px) scale(${state.scale})`;
    }
    if (reset) reset.textContent = `${Math.round(state.scale * 100)} %`;
  }

  function resetTransform() {
    state.scale = 1;
    state.translateX = 0;
    state.translateY = 0;
    applyTransform();
  }

  async function toggleFullscreen() {
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
    const button = byId("awc-satellite-fullscreen");
    if (!button) return;
    button.textContent = document.fullscreenElement ? "Vollbild schließen" : "Vollbild";
    resetTransform();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount, { once: true });
  } else {
    mount();
  }
})();
