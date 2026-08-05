(() => {
  "use strict";

  const API = "/api/v1";
  const SNAPSHOT_MAX_AGE_MS = 120000;
  const stateInfo = {
    GREEN: { symbol: "✓", label: "GRÜN", title: "GRÜN – Aufnahmebetrieb möglich" },
    YELLOW: { symbol: "⚠", label: "GELB", title: "GELB – erhöhte Aufmerksamkeit" },
    RED: { symbol: "⛔", label: "ROT", title: "ROT – Hardware schützen" },
    UNKNOWN: { symbol: "?", label: "UNBEKANNT", title: "UNBEKANNT – nicht auf Sicherheit verlassen" },
  };

  const root = document.createElement("section");
  root.id = "awc-live-panel";
  root.dataset.state = "UNKNOWN";
  root.setAttribute("aria-labelledby", "awc-live-title");
  root.innerHTML = `
    <header class="awc-header">
      <div>
        <p class="awc-eyebrow">LOKALE LIVE-ÜBERWACHUNG</p>
        <h2 id="awc-live-title"><span id="awc-state-symbol" aria-hidden="true">?</span> <span id="awc-state">UNBEKANNT</span></h2>
      </div>
      <p id="awc-connection" class="awc-connection" role="status">Verbindung wird aufgebaut …</p>
    </header>
    <p id="awc-action" class="awc-action" role="alert" aria-live="assertive">Nicht auf diesen Zustand verlassen.</p>
    <div class="awc-grid">
      <article class="awc-card">
        <h3>Entscheidung</h3>
        <dl>
          <div><dt>Datenqualität</dt><dd id="awc-quality">UNBEKANNT</dd></div>
          <div><dt>Snapshot</dt><dd id="awc-snapshot">–</dd></div>
          <div><dt>Letzte Bewertung</dt><dd id="awc-time">–</dd></div>
          <div><dt>Ausrüstung</dt><dd id="awc-equipment">UNBEKANNT</dd></div>
          <div><dt>Früheste Entwarnung</dt><dd id="awc-clear">–</dd></div>
        </dl>
        <p id="awc-reasons" class="awc-detail">Keine belastbaren Gründe verfügbar.</p>
      </article>
      <article class="awc-card">
        <h3>Kernquellen</h3>
        <div id="awc-sources" class="awc-source-list"></div>
      </article>
      <article class="awc-card">
        <h3>Radar am Standort</h3>
        <dl>
          <div><dt>Nächste Fläche</dt><dd id="awc-radar-distance">unbekannt</dd></div>
          <div><dt>Richtung</dt><dd id="awc-radar-direction">unbekannt</dd></div>
          <div><dt>Ankunft</dt><dd id="awc-radar-arrival">unbekannt</dd></div>
          <div><dt>Standortintensität</dt><dd id="awc-radar-site">unbekannt</dd></div>
          <div><dt>Spitzenintensität</dt><dd id="awc-radar-peak">unbekannt</dd></div>
          <div><dt>Abdeckung 0–120 min</dt><dd id="awc-radar-coverage">unbekannt</dd></div>
        </dl>
      </article>
      <article class="awc-card">
        <h3>Amtliche Warnungen</h3>
        <div id="awc-warnings" class="awc-list-empty">Keine belastbaren Warnungsdaten.</div>
      </article>
      <article class="awc-card">
        <h3>Gefahren-Latches</h3>
        <div id="awc-hazards" class="awc-list-empty">Keine aktiven Latches.</div>
      </article>
      <article class="awc-card">
        <h3>Alarm</h3>
        <p id="awc-alert">Kein aktiver Alarm.</p>
        <p id="awc-alarm-capability" class="awc-detail">Akustik und Browserbenachrichtigungen sind noch nicht aktiviert.</p>
      </article>
    </div>
    <div class="awc-controls" aria-label="Live-Überwachung steuern">
      <button id="awc-enable-alerts" type="button">Alarme aktivieren</button>
      <button id="awc-refresh" type="button">Alle Quellen aktualisieren</button>
      <button id="awc-ack" type="button" disabled>Alarm quittieren</button>
      <a href="/api/v1/diagnostics" target="_blank" rel="noopener">Diagnose öffnen</a>
    </div>
  `;
  document.body.insertBefore(root, document.body.firstChild);

  const byId = (id) => document.getElementById(id);
  const text = (id, value) => {
    const element = byId(id);
    if (element) element.textContent = value;
  };
  const formatTime = (value) => {
    if (!value) return "unbekannt";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "unbekannt";
    return new Intl.DateTimeFormat("de-DE", { dateStyle: "short", timeStyle: "medium" }).format(date);
  };
  const formatAge = (seconds) => {
    if (!Number.isFinite(seconds)) return "Alter unbekannt";
    if (seconds < 60) return `${Math.max(0, Math.round(seconds))} s alt`;
    return `${Math.round(seconds / 60)} min alt`;
  };
  const formatNumber = (value, suffix = "") =>
    Number.isFinite(value) ? `${new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 }).format(value)}${suffix}` : "unbekannt";
  const sourceById = (snapshot, sourceId) =>
    (snapshot.sources || []).find((item) => item.sourceId === sourceId) || null;

  let runtimeConfig = {
    browserAudioEnabled: true,
    browserNotificationsEnabled: true,
  };
  let csrfToken = null;
  let currentSnapshot = null;
  let currentAlert = null;
  let audioContext = null;
  let alarmsEnabled = localStorage.getItem("awc-alerts-enabled") === "1";
  let lastAlertEventId = null;
  let stream = null;
  let connected = false;

  function setConnection(message, state) {
    text("awc-connection", message);
    byId("awc-connection").dataset.connection = state;
  }

  function statePresentation(state) {
    return stateInfo[state] || stateInfo.UNKNOWN;
  }

  function renderSources(snapshot) {
    const container = byId("awc-sources");
    container.replaceChildren();
    for (const sourceId of ["DWD_RV", "DWD_CAP", "DWD_WN", "LOCAL_PERSISTENCE", "RAIN_SENSOR"]) {
      const item = sourceById(snapshot, sourceId);
      if (!item) continue;
      const row = document.createElement("div");
      row.className = "awc-source";
      row.dataset.state = item.state || "UNKNOWN";
      const name = document.createElement("strong");
      name.textContent = sourceId;
      const status = document.createElement("span");
      status.textContent = `${item.state || "UNKNOWN"} · ${formatAge(item.ageSeconds)}`;
      row.append(name, status);
      if (item.failureCode || item.failureMessage) {
        const error = document.createElement("small");
        error.textContent = [item.failureCode, item.failureMessage].filter(Boolean).join(": ");
        row.append(error);
      }
      container.append(row);
    }
  }

  function renderRadar(snapshot) {
    const radar = sourceById(snapshot, "DWD_RV");
    const payload = radar?.payload || {};
    text("awc-radar-distance", formatNumber(payload.nearestPrecipitationDistanceKm, " km"));
    text("awc-radar-direction", payload.nearestPrecipitationDirection || "unbekannt");
    text("awc-radar-arrival", Number.isFinite(payload.arrivalMinutes) ? `${Math.round(payload.arrivalMinutes)} min` : "unbekannt");
    text("awc-radar-site", formatNumber(payload.siteIntensity, payload.intensityUnit ? ` ${payload.intensityUnit}` : ""));
    text("awc-radar-peak", formatNumber(payload.peakIntensity, payload.intensityUnit ? ` ${payload.intensityUnit}` : ""));
    text("awc-radar-coverage", payload.coverage0To120 === true ? "vollständig" : payload.coverage0To120 === false ? "unvollständig" : "unbekannt");
  }

  function renderWarnings(snapshot) {
    const container = byId("awc-warnings");
    const warnings = sourceById(snapshot, "DWD_CAP")?.payload?.active || [];
    container.replaceChildren();
    if (!warnings.length) {
      container.className = "awc-list-empty";
      container.textContent = "Keine aktiven amtlichen Warnungen im aktuellen Snapshot.";
      return;
    }
    container.className = "awc-item-list";
    for (const warning of warnings) {
      const item = document.createElement("article");
      const heading = document.createElement("strong");
      heading.textContent = warning.headline || warning.event || "Amtliche Warnung";
      const meta = document.createElement("small");
      meta.textContent = [warning.event, warning.severity, warning.expires ? `bis ${formatTime(warning.expires)}` : null]
        .filter(Boolean)
        .join(" · ");
      item.append(heading, meta);
      container.append(item);
    }
  }

  function renderHazards(snapshot) {
    const container = byId("awc-hazards");
    const hazards = snapshot.activeHazards || [];
    container.replaceChildren();
    byId("awc-ack").disabled = hazards.length === 0;
    if (!hazards.length) {
      container.className = "awc-list-empty";
      container.textContent = "Keine aktiven Latches.";
      return;
    }
    container.className = "awc-item-list";
    for (const hazard of hazards) {
      const item = document.createElement("article");
      const signal = hazard.signal || hazard;
      const heading = document.createElement("strong");
      heading.textContent = `${signal.state || "GEFAHR"}: ${signal.reasonCode || signal.hazardKey || "Latch"}`;
      const detail = document.createElement("small");
      detail.textContent = `${signal.reason || "Gefahr bleibt konservativ aktiv."} · Haltezeit bis ${formatTime(signal.holdUntil)}`;
      item.append(heading, detail);
      container.append(item);
    }
  }

  function renderSnapshot(snapshot) {
    currentSnapshot = snapshot;
    const risk = snapshot.hardwareRisk || {};
    const state = stateInfo[risk.state] ? risk.state : "UNKNOWN";
    const presentation = statePresentation(state);
    root.dataset.state = state;
    text("awc-state-symbol", presentation.symbol);
    text("awc-state", presentation.title);
    text("awc-action", risk.action || "UNKNOWN_DO_NOT_RELY");
    text("awc-quality", risk.dataQuality || "UNKNOWN");
    text("awc-snapshot", snapshot.snapshotId || "unbekannt");
    text("awc-time", formatTime(snapshot.evaluationAt));
    text("awc-equipment", snapshot.equipmentState || "UNKNOWN");
    text("awc-clear", formatTime(snapshot.earliestPossibleClearAt));
    const reasons = risk.reasons || [];
    const codes = risk.reasonCodes || [];
    text("awc-reasons", [...reasons, codes.length ? `Codes: ${codes.join(", ")}` : null].filter(Boolean).join(" · ") || "Keine zusätzlichen Gründe.");
    renderSources(snapshot);
    renderRadar(snapshot);
    renderWarnings(snapshot);
    renderHazards(snapshot);
    const prefix = state === "GREEN" ? "" : `${presentation.label} – `;
    document.title = `${prefix}Astro-Wolkencheck`;
  }

  function renderAlert(alert) {
    currentAlert = alert || null;
    if (!alert) {
      text("awc-alert", "Kein aktiver Alarm.");
      return;
    }
    const acknowledged = Boolean(alert.acknowledgedAt) || alert.requiresAttention === false;
    text(
      "awc-alert",
      `${alert.eventType || "ALARM"} · ${alert.riskState || "UNKNOWN"}${acknowledged ? " · quittiert" : " · Aufmerksamkeit erforderlich"}`,
    );
  }

  function updateAlarmCapability() {
    const notifications = !runtimeConfig.browserNotificationsEnabled
      ? "Browserbenachrichtigungen deaktiviert"
      : typeof Notification === "undefined"
        ? "Browserbenachrichtigungen nicht verfügbar"
        : Notification.permission === "granted"
          ? "Browserbenachrichtigungen erlaubt"
          : `Browserbenachrichtigungen: ${Notification.permission}`;
    text(
      "awc-alarm-capability",
      `${alarmsEnabled ? "Akustische Alarme aktiviert" : "Akustische Alarme nicht aktiviert"} · ${notifications}`,
    );
    byId("awc-enable-alerts").textContent = alarmsEnabled ? "Alarme aktiviert" : "Alarme aktivieren";
  }

  function ensureAudioContext() {
    if (!runtimeConfig.browserAudioEnabled) return null;
    const AudioCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtor) return null;
    if (!audioContext) audioContext = new AudioCtor();
    return audioContext;
  }

  function tone(frequency, start, duration) {
    const context = ensureAudioContext();
    if (!context) return;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "square";
    oscillator.frequency.setValueAtTime(frequency, start);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.18, start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start(start);
    oscillator.stop(start + duration + 0.02);
  }

  function playAlert(riskState) {
    if (!alarmsEnabled || !runtimeConfig.browserAudioEnabled) return;
    const context = ensureAudioContext();
    if (!context || context.state === "suspended") return;
    const start = context.currentTime + 0.02;
    if (riskState === "RED") {
      tone(740, start, 0.22);
      tone(520, start + 0.30, 0.22);
      tone(740, start + 0.60, 0.34);
    } else if (riskState === "YELLOW") {
      tone(660, start, 0.18);
      tone(660, start + 0.28, 0.18);
    }
  }

  function notify(alert) {
    if (!runtimeConfig.browserNotificationsEnabled || typeof Notification === "undefined") return;
    if (Notification.permission !== "granted") return;
    const state = alert.riskState || "UNKNOWN";
    const title = state === "RED" ? "Astro-Wolkencheck: ROT" : "Astro-Wolkencheck: GELB";
    const body = (alert.reasonCodes || []).join(", ") || "Neue sicherheitsrelevante Zustandsänderung.";
    new Notification(title, { body, tag: alert.eventId || alert.snapshotId, requireInteraction: state === "RED" });
  }

  function handleAlert(alert) {
    renderAlert(alert);
    if (!alert || !alert.requiresAttention || alert.eventId === lastAlertEventId) return;
    lastAlertEventId = alert.eventId;
    playAlert(alert.riskState);
    notify(alert);
  }

  function disconnected(reason) {
    connected = false;
    setConnection(reason, "DISCONNECTED");
    if (!currentSnapshot || currentSnapshot.hardwareRisk?.state === "GREEN") {
      renderSnapshot({
        snapshotId: currentSnapshot?.snapshotId || "nicht verbunden",
        evaluationAt: new Date().toISOString(),
        equipmentState: currentSnapshot?.equipmentState || "UNKNOWN",
        hardwareRisk: {
          state: "UNKNOWN",
          dataQuality: "UNAVAILABLE",
          action: "UNKNOWN_DO_NOT_RELY",
          reasons: ["Die lokale Live-Verbindung ist unterbrochen."],
          reasonCodes: ["LOCAL_API_DISCONNECTED"],
        },
        activeHazards: [],
        sources: [{ sourceId: "LOCAL_API", state: "FAILED", ageSeconds: 0, isComplete: false }],
      });
    }
  }

  async function getToken() {
    if (csrfToken) return csrfToken;
    const response = await fetch(`${API}/security/csrf`, { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) throw new Error("CSRF token unavailable");
    csrfToken = (await response.json()).token;
    return csrfToken;
  }

  async function load() {
    try {
      const [safetyResponse, alertsResponse] = await Promise.all([
        fetch(`${API}/safety`, { cache: "no-store" }),
        fetch(`${API}/alerts`, { cache: "no-store" }),
      ]);
      if (!safetyResponse.ok || !alertsResponse.ok) throw new Error("local API unavailable");
      renderSnapshot(await safetyResponse.json());
      const alerts = await alertsResponse.json();
      handleAlert(alerts.active);
      connected = true;
      setConnection("Lokale API verbunden", "CONNECTED");
    } catch (_error) {
      disconnected("Lokale API nicht erreichbar");
    }
  }

  async function post(path) {
    const button = path.includes("acknowledge") ? byId("awc-ack") : byId("awc-refresh");
    button.disabled = true;
    try {
      const response = await fetch(path, {
        method: "POST",
        headers: { "X-CSRF-Token": await getToken() },
        credentials: "same-origin",
      });
      if (response.status === 403) csrfToken = null;
      if (!response.ok) throw new Error(`request failed: ${response.status}`);
      await load();
    } catch (_error) {
      disconnected("Schreibzugriff auf lokale API fehlgeschlagen");
    } finally {
      if (button === byId("awc-refresh") || currentSnapshot?.activeHazards?.length) button.disabled = false;
    }
  }

  function parseEvent(event) {
    try {
      return JSON.parse(event.data).payload;
    } catch (_error) {
      return null;
    }
  }

  function connectEvents() {
    stream = new EventSource(`${API}/events`);
    stream.onopen = () => {
      connected = true;
      setConnection("SSE verbunden", "CONNECTED");
    };
    stream.addEventListener("snapshot", (event) => {
      const payload = parseEvent(event);
      if (payload) renderSnapshot(payload);
    });
    stream.addEventListener("alert", (event) => {
      const payload = parseEvent(event);
      if (payload) handleAlert(payload);
    });
    stream.addEventListener("acknowledgement", (event) => {
      const payload = parseEvent(event);
      if (payload) renderAlert(payload);
    });
    stream.addEventListener("source-state", () => {
      void load();
    });
    stream.addEventListener("runtime-status", () => {
      connected = true;
      setConnection("Runtime aktiv", "CONNECTED");
    });
    stream.onerror = () => {
      disconnected("SSE-Verbindung unterbrochen – neuer Abruf läuft");
      window.setTimeout(() => void load(), 750);
    };
  }

  async function enableAlerts() {
    alarmsEnabled = true;
    localStorage.setItem("awc-alerts-enabled", "1");
    const context = ensureAudioContext();
    if (context?.state === "suspended") await context.resume();
    if (
      runtimeConfig.browserNotificationsEnabled &&
      typeof Notification !== "undefined" &&
      Notification.permission === "default"
    ) {
      await Notification.requestPermission();
    }
    updateAlarmCapability();
  }

  byId("awc-enable-alerts").addEventListener("click", () => void enableAlerts());
  byId("awc-refresh").addEventListener("click", () => void post(`${API}/runtime/refresh`));
  byId("awc-ack").addEventListener("click", () => void post(`${API}/alerts/acknowledge`));
  window.addEventListener("offline", () => disconnected("Browser ist offline"));
  window.addEventListener("online", () => void load());
  window.addEventListener("beforeunload", () => stream?.close());

  window.setInterval(() => {
    const evaluated = currentSnapshot?.evaluationAt ? new Date(currentSnapshot.evaluationAt).getTime() : 0;
    if (connected && currentSnapshot?.hardwareRisk?.state === "GREEN" && Date.now() - evaluated > SNAPSHOT_MAX_AGE_MS) {
      disconnected("Live-Snapshot wurde nicht rechtzeitig erneuert");
    }
  }, 15000);

  fetch("/runtime-config.json", { cache: "no-store" })
    .then((response) => (response.ok ? response.json() : {}))
    .then((config) => {
      runtimeConfig = { ...runtimeConfig, ...config };
      updateAlarmCapability();
    })
    .catch(() => updateAlarmCapability());

  updateAlarmCapability();
  connectEvents();
  void load();
})();
