(() => {
  "use strict";

  const API = "/api/v1";
  const MAX_SNAPSHOT_AGE_MS = 120000;
  const STORAGE_KEYS = Object.freeze({
    alerts: "awc-alerts-enabled",
    trustedSnapshot: "awc-ui-last-trusted-snapshot-v4.3",
  });

  const LIVE_PRESENTATION = Object.freeze({
    GREEN: {
      icon: "✓",
      title: "Kein aktuelles Live-Wetter-Veto erkannt",
      short: "Kein Wetter-Veto",
      detail: "Radar und amtliche Warnungen melden aktuell kein hardwarekritisches Live-Veto.",
      tone: "GREEN",
    },
    YELLOW: {
      icon: "!",
      title: "Erhöhte Aufmerksamkeit – Bedingungen erneut prüfen",
      short: "Erhöhte Aufmerksamkeit",
      detail: "Mindestens ein bestätigter oder vorsorglich gehaltener Hinweis erfordert eine erneute Prüfung.",
      tone: "YELLOW",
    },
    RED: {
      icon: "!",
      title: "Ausrüstung schützen – unmittelbare Gefahr erkannt",
      short: "Unmittelbare Gefahr",
      detail: "Eine bestätigte oder vorsorglich gehaltene Gefahr erfordert sofortiges Handeln.",
      tone: "RED",
    },
    UNKNOWN: {
      icon: "?",
      title: "Live-Sicherheitslage nicht zuverlässig beurteilbar",
      short: "Live-Lage unbekannt",
      detail: "Mindestens eine erforderliche Quelle ist nicht frisch, vollständig oder verfügbar.",
      tone: "UNKNOWN",
    },
  });

  const SOURCE_LABELS = Object.freeze({
    DWD_RV: "DWD-Regenradar",
    DWD_CAP: "Amtliche DWD-Warnungen",
    DWD_WN: "DWD-Niederschlagsanalyse",
    LOCAL_PERSISTENCE: "Lokaler Sicherheitsspeicher",
    RAIN_SENSOR: "Regensensor am Teleskop",
  });

  const EQUIPMENT_LABELS = Object.freeze({
    NOT_DEPLOYED: "Noch nicht aufgebaut",
    DEPLOYED_ATTENDED: "Aufgebaut und beaufsichtigt",
    DEPLOYED_UNATTENDED: "Aufgebaut und unbeaufsichtigt",
    UNKNOWN: "Noch nicht festgelegt",
  });

  const EQUIPMENT_HELP = Object.freeze({
    NOT_DEPLOYED: "Bei Gefahr wird vom Aufbau abgeraten.",
    DEPLOYED_ATTENDED: "Bei Gefahr wird sofortiges Prüfen und Schützen empfohlen.",
    DEPLOYED_UNATTENDED: "Gefahren erfordern besonders dringende Aufmerksamkeit.",
    UNKNOWN: "Die Wettererkennung bleibt unverändert; die Handlungsempfehlung ist weniger spezifisch.",
  });

  const HARDWARE_TERMS = Object.freeze([
    "GEWITTER",
    "STARKREGEN",
    "DAUERREGEN",
    "REGEN",
    "WIND",
    "STURM",
    "ORKAN",
    "HAGEL",
    "BÖEN",
  ]);

  const state = {
    snapshot: null,
    previousTrusted: null,
    alert: null,
    csrfToken: null,
    runtimeConfig: {
      browserAudioEnabled: true,
      browserNotificationsEnabled: true,
    },
    connection: "CONNECTING",
    stream: null,
    alarmsEnabled: safeStorageGet(STORAGE_KEYS.alerts) === "1",
    audioContext: null,
    lastAlertEventId: null,
    forecast: {
      title: "Forecast wird geladen",
      detail: "Die Aufnahmequalität wird unabhängig von der Live-Sicherheit bewertet.",
      tone: "INFO",
      confidence: "Noch nicht verfügbar",
    },
  };

  const root = document.createElement("section");
  root.id = "live-dashboard-root";
  root.setAttribute("aria-label", "Live-Sicherheit und aktuelle Handlungsempfehlung");

  function safeStorageGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function safeStorageSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // Storage is optional. Runtime safety never depends on browser storage.
    }
  }

  function byId(id) {
    return document.getElementById(id);
  }

  function text(id, value) {
    const node = byId(id);
    if (node) node.textContent = value;
  }

  function finite(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function formatNumber(value, unit = "", digits = 1) {
    if (!finite(value)) return "Noch nicht verfügbar";
    const formatted = new Intl.NumberFormat("de-DE", {
      maximumFractionDigits: digits,
    }).format(value);
    return `${formatted}${unit ? ` ${unit}` : ""}`;
  }

  function formatAge(seconds) {
    if (!finite(seconds)) return "Alter nicht verfügbar";
    const safe = Math.max(0, seconds);
    if (safe < 60) return `${Math.round(safe)} Sek. alt`;
    if (safe < 3600) return `${Math.round(safe / 60)} Min. alt`;
    return `${Math.round(safe / 360) / 10} Std. alt`;
  }

  function formatTime(value, options = {}) {
    if (!value) return options.empty || "Noch nicht verfügbar";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return options.empty || "Noch nicht verfügbar";
    return new Intl.DateTimeFormat("de-DE", {
      timeZone: "Europe/Berlin",
      day: options.date === false ? undefined : "2-digit",
      month: options.date === false ? undefined : "2-digit",
      year: options.year ? "numeric" : undefined,
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function sourceById(snapshot, sourceId) {
    return (snapshot?.sources || []).find((source) => source.sourceId === sourceId) || null;
  }

  function sourceStateLabel(source) {
    const labels = {
      LIVE: "frisch",
      STALE: "veraltet",
      FAILED: "fehlgeschlagen",
      INITIALIZING: "wird geladen",
      DISABLED: "nicht aktiviert",
      NOT_CONFIGURED: "nicht konfiguriert",
      NOT_AVAILABLE: "noch nicht integriert",
    };
    return labels[source?.state] || "nicht beurteilbar";
  }

  function livePresentation(snapshot) {
    const key = snapshot?.hardwareRisk?.state;
    return LIVE_PRESENTATION[key] || LIVE_PRESENTATION.UNKNOWN;
  }

  function normalizeRadar(snapshot) {
    const source = sourceById(snapshot, "DWD_RV");
    const payload = source?.payload || {};
    const timeline = Array.isArray(payload.frameTimeline)
      ? payload.frameTimeline
      : Array.isArray(payload.timeline)
        ? payload.timeline
        : [];
    return {
      source,
      state: source?.state || "INITIALIZING",
      ageSeconds: source?.ageSeconds,
      cycleTime: payload.cycleTime || source?.cycleTime,
      frameCount: finite(payload.frameCount) ? payload.frameCount : null,
      horizonMinutes: finite(payload.forecastHorizonMinutes) ? payload.forecastHorizonMinutes : null,
      coverage0To60: payload.coverage0To60 === true,
      coverage0To120: payload.coverage0To120 === true,
      rainNow: payload.rainNow === true,
      movingTowardSite: payload.movingTowardSite === true,
      arrivalMinutes: finite(payload.arrivalMinutes) ? payload.arrivalMinutes : null,
      arrivalWindow: payload.arrivalWindow || null,
      distanceKm: finite(payload.nearestPrecipitationDistanceKm)
        ? payload.nearestPrecipitationDistanceKm
        : null,
      direction: payload.nearestPrecipitationDirection || null,
      areaKm2: finite(payload.affectedAreaKm2) ? payload.affectedAreaKm2 : null,
      siteIntensity: finite(payload.siteIntensityMm5Min) ? payload.siteIntensityMm5Min : null,
      peakIntensity: finite(payload.peakIntensityMm5Min) ? payload.peakIntensityMm5Min : null,
      peakLeadMinutes: finite(payload.peakIntensityLeadMinutes)
        ? payload.peakIntensityLeadMinutes
        : null,
      unit: payload.unit || "mm/5 min",
      timeline,
    };
  }

  function radarArrivalCopy(radar) {
    if (finite(radar.arrivalMinutes)) {
      const confidence = radar.arrivalWindow?.confidence
        ? ` · ${radar.arrivalWindow.confidence}`
        : "";
      return {
        value: `in etwa ${Math.round(radar.arrivalMinutes)} Min.`,
        note: radar.arrivalWindow
          ? `Mögliches Fenster ${Math.round(radar.arrivalWindow.earliestMinutes)}–${Math.round(radar.arrivalWindow.latestMinutes)} Min.${confidence}`
          : "Aus den aktuellen Radarframes abgeleitet.",
      };
    }
    if (radar.coverage0To120 && !radar.rainNow) {
      return {
        value: "Keine Ankunft in 120 Min.",
        note: "Der vollständige Vorhersagehorizont zeigt keinen Regen am Standort.",
      };
    }
    return {
      value: "Noch nicht beurteilbar",
      note: "Für eine belastbare Aussage fehlen vollständige Radarframes.",
    };
  }

  function radarMovementCopy(radar) {
    if (radar.movingTowardSite) {
      return {
        value: "Zieht zum Standort",
        note: "Die Entfernung nimmt in den Radarframes belastbar ab.",
      };
    }
    if (radar.distanceKm !== null && radar.coverage0To120) {
      return {
        value: "Zieht nicht zum Standort",
        note: "Im aktuellen Horizont ist kein belastbarer Ankunftstrend erkennbar.",
      };
    }
    return {
      value: "Noch nicht beurteilbar",
      note: "Bewegungsdaten sind noch nicht vollständig.",
    };
  }

  function warningHardwareRelevance(warning) {
    const event = String(warning?.event || warning?.headline || "").toUpperCase();
    const relevant = HARDWARE_TERMS.some((term) => event.includes(term));
    if (!relevant) {
      return {
        tone: "INFO",
        label: "Keine direkte Hardware-Sperre",
        description: "Die amtliche Warnung bleibt sichtbar, erzeugt aber kein unmittelbares Regen- oder Wind-Veto für die Teleskophardware.",
      };
    }
    const severity = String(warning?.severity || "Unknown");
    const tone = ["Severe", "Extreme"].includes(severity) ? "RED" : "YELLOW";
    return {
      tone,
      label: tone === "RED" ? "Hardware-Relevanz: ROT" : "Hardware-Relevanz: GELB",
      description: tone === "RED"
        ? "Ausrüstung nicht aufbauen beziehungsweise sofort schützen."
        : "Bedingungen und Ausrüstung erneut prüfen.",
    };
  }

  function deriveForecastState() {
    const hero = document.querySelector("#overviewContent .decision-hero");
    if (!hero) return state.forecast;
    const title = hero.querySelector("h2")?.textContent?.trim() || "Forecast wird geladen";
    const risk = [...hero.querySelectorAll(".decision-facts > div")]
      .find((item) => item.querySelector("span")?.textContent?.trim() === "Größtes Risiko")
      ?.querySelector("strong")?.textContent?.trim();
    const confidence = [...hero.querySelectorAll(".decision-facts > div")]
      .find((item) => item.querySelector("span")?.textContent?.trim() === "Sicherheit")
      ?.querySelector("strong")?.textContent?.trim();
    const tone = hero.classList.contains("good")
      ? "GREEN"
      : hero.classList.contains("bad")
        ? "RED"
        : hero.classList.contains("warn")
          ? "YELLOW"
          : "INFO";
    return {
      title,
      tone,
      detail: risk ? `Größtes Forecast-Risiko: ${risk}.` : "Forecast und Live-Sicherheit werden getrennt bewertet.",
      confidence: confidence || "Noch nicht verfügbar",
    };
  }

  function dataConfidence(snapshot) {
    const risk = snapshot?.hardwareRisk || {};
    const required = ["DWD_RV", "DWD_CAP", "LOCAL_PERSISTENCE"]
      .map((id) => sourceById(snapshot, id))
      .filter(Boolean);
    const allLive = required.length === 3 && required.every((source) => source.state === "LIVE");
    if (risk.dataQuality === "COMPLETE" && allLive) {
      return {
        tone: "GREEN",
        icon: "✓",
        title: "Live-Daten vollständig",
        detail: "Radar, Warnungen und lokaler Sicherheitsspeicher sind frisch und vollständig.",
      };
    }
    if (risk.dataQuality === "DEGRADED") {
      return {
        tone: "YELLOW",
        icon: "!",
        title: "Live-Daten eingeschränkt",
        detail: "Die Kernentscheidung ist möglich, mindestens eine unterstützende Quelle ist aber beeinträchtigt.",
      };
    }
    return {
      tone: "RED",
      icon: "?",
      title: "Live-Daten nicht ausreichend",
      detail: "Mindestens eine erforderliche Quelle fehlt, ist veraltet oder fehlgeschlagen.",
    };
  }

  function actionRecommendation(snapshot, forecast, radar) {
    const live = livePresentation(snapshot);
    const equipment = snapshot?.equipmentState || "UNKNOWN";
    const equipmentKnown = equipment !== "UNKNOWN";
    let title = live.title;
    let body = live.detail;

    if (live.tone === "GREEN") {
      title = forecast.tone === "GREEN"
        ? "Live sicher und Forecast günstig – Aufbau grundsätzlich möglich"
        : "Live kein Wetter-Veto – Aufnahmequalität getrennt prüfen";
      body = `Aus aktueller Live-Wettersicht besteht kein hardwarekritisches Veto. Der ausgewählte Forecast lautet „${forecast.title}“. `;
      body += equipmentKnown
        ? `Ausrüstungsmodus: ${EQUIPMENT_LABELS[equipment]}.`
        : "Lege vor dem Aufbau den Ausrüstungsstatus fest und prüfe Radar sowie Himmel vor Ort erneut.";
    } else if (live.tone === "YELLOW") {
      title = equipment === "DEPLOYED_UNATTENDED"
        ? "Unbeaufsichtigte Ausrüstung sofort kontrollieren"
        : "Bedingungen erneut prüfen und vorbereitet bleiben";
      body = live.detail;
    } else if (live.tone === "RED") {
      title = equipment === "NOT_DEPLOYED"
        ? "Nicht aufbauen"
        : "Ausrüstung sofort prüfen und schützen";
      body = live.detail;
    } else {
      title = equipment === "NOT_DEPLOYED"
        ? "Nicht auf die Live-Freigabe verlassen"
        : "Ausrüstung vorsorglich sofort kontrollieren";
      body = live.detail;
    }

    const radarTag = radar.rainNow
      ? { text: "Regen am Standort erkannt", tone: "RED" }
      : radar.coverage0To120
        ? { text: "Radar 0–120 Min. vollständig", tone: "GREEN" }
        : { text: "Radar noch unvollständig", tone: "YELLOW" };

    return {
      title,
      body,
      tone: live.tone,
      tags: [
        radarTag,
        { text: forecast.title, tone: forecast.tone },
        {
          text: equipmentKnown ? EQUIPMENT_LABELS[equipment] : "Ausrüstungsstatus festlegen",
          tone: equipmentKnown ? "INFO" : "YELLOW",
        },
      ],
    };
  }

  function prepareForecastShell() {
    const app = document.querySelector(".app");
    if (!app) return false;
    const header = app.querySelector(".app-header");
    const tabs = app.querySelector(".tabs-shell");
    if (!header || !tabs) return false;

    header.classList.add("awc-unified-header");
    const title = header.querySelector("h1");
    if (title) title.textContent = "Astro-Wolkencheck · Geiselhöring";
    const location = header.querySelector(".location-line");
    if (location) location.textContent = "Lokale Sicherheitslage und astronomische Nachtplanung";

    let meta = header.querySelector(".awc-header-meta");
    if (!meta) {
      meta = document.createElement("div");
      meta.className = "awc-header-meta";
      meta.innerHTML = `
        <span id="awc-api-pill" class="awc-api-pill" data-connection="CONNECTING">Lokale API wird verbunden</span>
        <span>Live: <strong id="awc-header-live-age">noch nicht geladen</strong></span>
        <span>Forecast: <strong id="awc-header-forecast-age">noch nicht geladen</strong></span>
        <span id="awc-local-clock" class="awc-local-clock">--:-- Uhr</span>
      `;
      header.querySelector(".brand-block")?.append(meta);
    }

    const labels = {
      overview: "JETZT",
      windows: "NACHT PLANEN",
      hours: "STUNDEN",
      data: "DATEN & DIAGNOSE",
    };
    for (const button of tabs.querySelectorAll(".tab-button")) {
      if (labels[button.dataset.tab]) button.textContent = labels[button.dataset.tab];
    }
    const select = tabs.querySelector("#mobileTabSelect");
    if (select) {
      for (const option of select.options) {
        if (labels[option.value]) option.textContent = labels[option.value];
      }
    }

    if (!root.isConnected) app.insertBefore(root, tabs);
    return true;
  }

  function mountShell() {
    root.innerHTML = `
      <div class="awc-axis-grid" aria-label="Drei getrennte Entscheidungsachsen">
        <article id="awc-live-axis" class="awc-axis-card" data-tone="UNKNOWN">
          <div class="awc-axis-label"><span>Live-Sicherheit</span><span id="awc-live-code">UNBEKANNT</span></div>
          <div class="awc-axis-status"><span id="awc-live-icon" class="awc-axis-icon" aria-hidden="true">?</span><div class="awc-axis-copy"><h2 id="awc-live-title">Live-Lage wird geladen</h2><p id="awc-live-detail">Radar und amtliche Warnungen werden geprüft.</p></div></div>
        </article>
        <article id="awc-forecast-axis" class="awc-axis-card" data-tone="INFO">
          <div class="awc-axis-label"><span>Aufnahmequalität</span><span>Forecast</span></div>
          <div class="awc-axis-status"><span class="awc-axis-icon" aria-hidden="true">◐</span><div class="awc-axis-copy"><h2 id="awc-forecast-title">Forecast wird geladen</h2><p id="awc-forecast-detail">Unabhängig von der Live-Sicherheit.</p></div></div>
        </article>
        <article id="awc-data-axis" class="awc-axis-card" data-tone="UNKNOWN">
          <div class="awc-axis-label"><span>Datenvertrauen</span><span id="awc-data-code">WIRD GELADEN</span></div>
          <div class="awc-axis-status"><span id="awc-data-icon" class="awc-axis-icon" aria-hidden="true">?</span><div class="awc-axis-copy"><h2 id="awc-data-title">Daten werden geprüft</h2><p id="awc-data-detail">Frische und Vollständigkeit der Kernquellen.</p></div></div>
        </article>
      </div>

      <div class="awc-decision-grid">
        <article id="awc-action-card" class="awc-card awc-action-card" data-tone="UNKNOWN" aria-live="polite">
          <p class="awc-eyebrow">Was soll ich jetzt tun?</p>
          <h2 id="awc-action-title">Live-Daten werden geladen</h2>
          <p id="awc-action-body">Bitte noch keine Entscheidung auf diese Anzeige stützen.</p>
          <div id="awc-action-tags" class="awc-tags"></div>
        </article>
        <article class="awc-card awc-equipment-card">
          <div class="awc-card-head"><div><h2>Ausrüstung</h2><p>Bestimmt die empfohlene Reaktion auf eine Gefahr.</p></div></div>
          <div id="awc-equipment-state" class="awc-equipment-state" data-known="false"><strong>Noch nicht festgelegt</strong><span>Die Wettererkennung bleibt davon unverändert.</span></div>
          <button id="awc-set-equipment" type="button">Ausrüstungsstatus festlegen</button>
        </article>
      </div>

      <div class="awc-live-grid">
        <article class="awc-card awc-radar-card">
          <div class="awc-radar-head"><div><p class="awc-eyebrow">Nächste 120 Minuten</p><h2>DWD-Regenradar am Standort</h2></div><span id="awc-radar-fresh" class="awc-fresh">wird geladen</span></div>
          <div class="awc-radar-summary">
            <div class="awc-radar-visual" role="img" aria-label="Vereinfachte standortbezogene Radardarstellung">
              <div class="awc-compass"><span class="awc-north">N</span><span class="awc-ring" data-radius="100"></span><span class="awc-ring" data-radius="50"></span><span class="awc-ring" data-radius="25"></span><span class="awc-ring" data-radius="10"></span><span class="awc-site-dot"></span><span id="awc-rain-cell" class="awc-rain-cell" hidden><strong id="awc-rain-cell-distance">–</strong><span id="awc-rain-cell-direction">–</span></span></div>
              <span class="awc-radar-caption">Vereinfachte standortbezogene Darstellung · Ringe 10/25/50/100 km</span>
            </div>
            <div id="awc-radar-metrics" class="awc-radar-metrics"></div>
          </div>
          <div class="awc-timeline">
            <div class="awc-timeline-head"><strong>Radarentwicklung 0–120 Minuten</strong><span id="awc-timeline-summary">Timeline wird vorbereitet</span></div>
            <div class="awc-timeline-track" aria-hidden="true"><span class="awc-timeline-line"></span><span id="awc-timeline-points" class="awc-timeline-points"></span></div>
            <div class="awc-timeline-labels"><span>Jetzt</span><span>+30</span><span>+60</span><span>+90</span><span>+120 Min.</span></div>
          </div>
        </article>

        <div class="awc-side-stack">
          <article class="awc-card awc-warnings-card"><div class="awc-card-head"><div><h2>Amtliche Warnungen</h2><p>Amtlicher Schweregrad und Hardware-Relevanz getrennt.</p></div><span id="awc-warning-count" class="awc-badge">0 aktiv</span></div><div id="awc-warning-list" class="awc-warning-list"></div></article>
          <article class="awc-card awc-sources-card"><div class="awc-card-head"><div><h2>Kernquellen</h2><p>Nur die drei für eine grüne Live-Entscheidung erforderlichen Quellen.</p></div></div><div id="awc-source-list" class="awc-source-list"></div><details><summary>Weitere Quellen</summary><div id="awc-optional-source-list" class="awc-source-list"></div></details></article>
          <article id="awc-hazard-card" class="awc-card awc-hazard-card" hidden><div class="awc-card-head"><div><h2>Gespeicherte Gefahr</h2><p>Quittieren beendet den Alarm, nicht die Gefahr.</p></div></div><ul id="awc-hazard-list" class="awc-hazard-list"></ul></article>
        </div>
      </div>

      <div class="awc-secondary-grid">
        <article class="awc-card awc-change-card"><div class="awc-card-head"><div><h2>Was hat sich geändert?</h2><p>Vergleich mit dem vorherigen vollständigen Live-Snapshot.</p></div></div><ul id="awc-change-list" class="awc-change-list"></ul></article>
        <article class="awc-card awc-alarm-card"><div class="awc-card-head"><div><h2>Alarmierung</h2><p>Ton und Browsermeldung müssen einmal freigegeben werden.</p></div></div><p id="awc-alarm-state" class="awc-alarm-state">Kein aktiver Alarm.</p><div class="awc-button-row"><button id="awc-enable-alerts" class="awc-primary" type="button">Alarme aktivieren</button><button id="awc-test-alarm" type="button">Alarm testen</button><button id="awc-ack" type="button" disabled>Alarm quittieren</button><button id="awc-refresh-live" type="button">Live-Daten aktualisieren</button></div></article>
      </div>

      <details class="awc-technical"><summary>Datenquellen und technische Diagnose</summary><div class="awc-technical-grid"><div><span>Snapshot</span><code id="awc-tech-snapshot">–</code></div><div><span>Algorithmus</span><strong id="awc-tech-algorithm">–</strong></div><div><span>Konfiguration</span><strong id="awc-tech-config">–</strong></div><div><span>Bewertet</span><strong id="awc-tech-time">–</strong></div><div><span>Reason-Codes</span><code id="awc-tech-reasons">keine</code></div><div><span>Diagnose</span><a href="/api/v1/diagnostics" target="_blank" rel="noopener">Vollständige JSON-Diagnose öffnen</a></div></div></details>

      <dialog id="awc-equipment-dialog" class="awc-equipment-dialog"><form method="dialog" class="awc-dialog-inner"><h2>Ausrüstungsstatus festlegen</h2><p>Der Status ändert nicht die Wetterdaten. Er bestimmt, welche Handlung bei einer Gefahr empfohlen wird.</p><div id="awc-equipment-options" class="awc-equipment-options"></div><div class="awc-dialog-actions"><button value="cancel" type="submit">Abbrechen</button></div></form></dialog>
      <span id="awc-urgent-announcement" class="sr-only" aria-live="assertive"></span>
    `;
  }

  function createTag(item) {
    const tag = document.createElement("span");
    tag.className = "awc-tag";
    tag.dataset.tone = item.tone || "INFO";
    tag.textContent = item.text;
    return tag;
  }

  function setAxis(prefix, presentation, code) {
    const card = byId(`awc-${prefix}-axis`);
    if (card) card.dataset.tone = presentation.tone;
    text(`awc-${prefix}-icon`, presentation.icon);
    text(`awc-${prefix}-title`, presentation.title);
    text(`awc-${prefix}-detail`, presentation.detail);
    if (code) text(`awc-${prefix}-code`, code);
  }

  function renderHeader(snapshot) {
    const pill = byId("awc-api-pill");
    if (pill) {
      pill.dataset.connection = state.connection;
      pill.textContent = state.connection === "CONNECTED"
        ? "Lokale API verbunden"
        : state.connection === "DISCONNECTED"
          ? "Lokale API getrennt"
          : "Lokale API wird verbunden";
    }
    text("awc-header-live-age", snapshot ? formatAge(Math.max(0, (Date.now() - new Date(snapshot.evaluationAt).getTime()) / 1000)) : "noch nicht geladen");
    const forecastUpdated = document.getElementById("updatedMetric")?.textContent?.trim();
    text("awc-header-forecast-age", forecastUpdated && forecastUpdated !== "noch nicht geladen" ? forecastUpdated : "noch nicht geladen");
  }

  function renderAxes(snapshot) {
    const live = livePresentation(snapshot);
    setAxis("live", live, snapshot?.hardwareRisk?.state || "UNBEKANNT");

    const forecast = deriveForecastState();
    state.forecast = forecast;
    const forecastAxis = byId("awc-forecast-axis");
    if (forecastAxis) forecastAxis.dataset.tone = forecast.tone;
    text("awc-forecast-title", forecast.title);
    text("awc-forecast-detail", `${forecast.detail} Sicherheit: ${forecast.confidence}.`);

    const confidence = dataConfidence(snapshot);
    const dataCard = byId("awc-data-axis");
    if (dataCard) dataCard.dataset.tone = confidence.tone;
    text("awc-data-icon", confidence.icon);
    text("awc-data-title", confidence.title);
    text("awc-data-detail", confidence.detail);
    text("awc-data-code", snapshot?.hardwareRisk?.dataQuality || "WIRD GELADEN");
  }

  function renderAction(snapshot, radar) {
    const action = actionRecommendation(snapshot, state.forecast, radar);
    const card = byId("awc-action-card");
    if (card) card.dataset.tone = action.tone;
    text("awc-action-title", action.title);
    text("awc-action-body", action.body);
    const tags = byId("awc-action-tags");
    tags?.replaceChildren(...action.tags.map(createTag));
  }

  function renderEquipment(snapshot) {
    const equipment = snapshot?.equipmentState || "UNKNOWN";
    const box = byId("awc-equipment-state");
    if (!box) return;
    box.dataset.known = String(equipment !== "UNKNOWN");
    box.querySelector("strong").textContent = EQUIPMENT_LABELS[equipment] || EQUIPMENT_LABELS.UNKNOWN;
    box.querySelector("span").textContent = EQUIPMENT_HELP[equipment] || EQUIPMENT_HELP.UNKNOWN;
    text("awc-set-equipment", equipment === "UNKNOWN" ? "Ausrüstungsstatus festlegen" : "Ausrüstungsstatus ändern");
  }

  function metric(label, value, note) {
    const item = document.createElement("div");
    item.className = "awc-metric";
    const labelNode = document.createElement("span");
    labelNode.textContent = label;
    const valueNode = document.createElement("strong");
    valueNode.textContent = value;
    const noteNode = document.createElement("small");
    noteNode.textContent = note;
    item.append(labelNode, valueNode, noteNode);
    return item;
  }

  function bearingForDirection(direction) {
    return ({
      N: 0,
      NO: 45,
      NE: 45,
      O: 90,
      E: 90,
      SO: 135,
      SE: 135,
      S: 180,
      SW: 225,
      W: 270,
      NW: 315,
    })[String(direction || "").toUpperCase()] ?? 90;
  }

  function renderRadarVisual(radar) {
    const cell = byId("awc-rain-cell");
    if (!cell) return;
    if (radar.distanceKm === null) {
      cell.hidden = true;
      return;
    }
    cell.hidden = false;
    const bearing = bearingForDirection(radar.direction) * Math.PI / 180;
    const radial = clamp(radar.distanceKm / 100, .18, .84) * 44;
    const left = 50 + Math.sin(bearing) * radial;
    const top = 50 - Math.cos(bearing) * radial;
    cell.style.left = `${left}%`;
    cell.style.top = `${top}%`;
    text("awc-rain-cell-distance", `${Math.round(radar.distanceKm)} km`);
    text("awc-rain-cell-direction", radar.direction || "Richtung offen");
  }

  function renderRadar(snapshot) {
    const radar = normalizeRadar(snapshot);
    const arrival = radarArrivalCopy(radar);
    const movement = radarMovementCopy(radar);
    const fresh = byId("awc-radar-fresh");
    if (fresh) {
      fresh.textContent = `${sourceStateLabel(radar.source)} · ${formatAge(radar.ageSeconds)}`;
      fresh.style.color = radar.state === "LIVE" ? "var(--awc-green)" : "var(--awc-red)";
    }

    const metrics = byId("awc-radar-metrics");
    metrics?.replaceChildren(
      metric("Am Standort jetzt", radar.rainNow ? "Niederschlag erkannt" : "Trocken", radar.siteIntensity !== null ? `${formatNumber(radar.siteIntensity, radar.unit)} am Standort` : "Intensität noch nicht verfügbar"),
      metric("Nächste Fläche", radar.distanceKm !== null ? `${formatNumber(radar.distanceKm, "km")} ${radar.direction || ""}`.trim() : "Keine Fläche ausgewertet", radar.areaKm2 !== null ? `Erkannte Fläche etwa ${formatNumber(radar.areaKm2, "km²", 0)}` : "Flächengröße noch nicht verfügbar"),
      metric("Bewegung", movement.value, movement.note),
      metric("Mögliche Ankunft", arrival.value, arrival.note),
      metric("Spitzenintensität", radar.peakIntensity !== null ? formatNumber(radar.peakIntensity, radar.unit) : "Noch nicht verfügbar", radar.peakLeadMinutes !== null ? `Spitze bei +${Math.round(radar.peakLeadMinutes)} Min.` : "Kein Spitzenzeitpunkt vorhanden"),
      metric("Radarzyklus", formatTime(radar.cycleTime, { date: false }), radar.frameCount !== null ? `${radar.frameCount} Frames · Horizont ${radar.horizonMinutes || 120} Min.` : "Zyklusdaten werden geladen"),
    );

    renderRadarVisual(radar);

    const points = byId("awc-timeline-points");
    const count = radar.timeline.length || Math.min(9, Math.max(0, radar.frameCount || 0));
    if (points) {
      points.replaceChildren(...Array.from({ length: count || 9 }, () => document.createElement("i")));
      points.style.gridTemplateColumns = `repeat(${count || 9}, 1fr)`;
    }
    text(
      "awc-timeline-summary",
      radar.timeline.length
        ? `${radar.timeline.length} validierte Zeitpunkte aus der API`
        : radar.frameCount !== null
          ? `${radar.frameCount}/${radar.frameCount} Radarframes vollständig · Detailtimeline folgt in Phase 4`
          : "Radarframes werden geladen",
    );
    return radar;
  }

  function renderWarnings(snapshot) {
    const source = sourceById(snapshot, "DWD_CAP");
    const warnings = Array.isArray(source?.payload?.active) ? source.payload.active : [];
    text("awc-warning-count", `${warnings.length} aktiv`);
    const list = byId("awc-warning-list");
    if (!list) return;
    list.replaceChildren();
    if (!warnings.length) {
      const empty = document.createElement("p");
      empty.className = "awc-empty";
      empty.textContent = source?.state === "LIVE"
        ? "Keine aktiven amtlichen Warnungen für den Standort."
        : "Warnungsdaten sind derzeit nicht zuverlässig verfügbar.";
      list.append(empty);
      return;
    }

    for (const warning of warnings) {
      const relevance = warningHardwareRelevance(warning);
      const article = document.createElement("article");
      article.className = "awc-warning";
      article.dataset.hardware = relevance.tone;
      const heading = document.createElement("strong");
      heading.textContent = warning.headline || warning.event || "Amtliche Warnung";
      const meta = document.createElement("div");
      meta.className = "awc-warning-meta";
      meta.append(
        createTag({ text: `Amtlich: ${warning.severity || "Schweregrad offen"}`, tone: ["Severe", "Extreme"].includes(warning.severity) ? "RED" : "YELLOW" }),
        createTag({ text: relevance.label, tone: relevance.tone }),
        createTag({ text: warning.expires ? `bis ${formatTime(warning.expires)}` : "keine feste Ablaufzeit", tone: "INFO" }),
      );
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "Beschreibung und Verhaltensempfehlung";
      const relevanceCopy = document.createElement("p");
      relevanceCopy.textContent = relevance.description;
      const description = document.createElement("p");
      description.textContent = warning.description || "Keine zusätzliche Beschreibung geliefert.";
      const instruction = document.createElement("p");
      instruction.textContent = warning.instruction || "Keine zusätzliche Handlungsanweisung geliefert.";
      details.append(summary, relevanceCopy, description, instruction);
      article.append(heading, meta, details);
      list.append(article);
    }
  }

  function renderSourceRow(source) {
    const row = document.createElement("div");
    row.className = "awc-source-row";
    row.dataset.state = source?.state || "UNKNOWN";
    const label = document.createElement("strong");
    label.textContent = SOURCE_LABELS[source?.sourceId] || source?.sourceId || "Quelle";
    const status = document.createElement("span");
    status.textContent = `${sourceStateLabel(source)} · ${formatAge(source?.ageSeconds)}`;
    const detail = document.createElement("small");
    detail.textContent = source?.failureMessage
      ? `Letzter Fehler: ${source.failureMessage}`
      : source?.isComplete
        ? `Vollständiger Zyklus · gültig bis ${formatTime(source.validUntil, { date: false, empty: "ohne feste Gültigkeitszeit" })}`
        : "Noch kein vollständiger vertrauenswürdiger Zyklus.";
    const bar = document.createElement("span");
    bar.className = "awc-freshness";
    const fill = document.createElement("i");
    const age = finite(source?.ageSeconds) ? source.ageSeconds : source?.invalidAfterSeconds;
    const invalid = finite(source?.invalidAfterSeconds) ? source.invalidAfterSeconds : 1;
    fill.style.width = `${clamp(100 - age / invalid * 100, 0, 100)}%`;
    bar.append(fill);
    row.append(label, status, detail, bar);
    return row;
  }

  function renderSources(snapshot) {
    const requiredIds = ["DWD_RV", "DWD_CAP", "LOCAL_PERSISTENCE"];
    const optionalIds = ["DWD_WN", "RAIN_SENSOR"];
    const required = requiredIds.map((id) => sourceById(snapshot, id)).filter(Boolean);
    const optional = optionalIds.map((id) => sourceById(snapshot, id)).filter(Boolean);
    byId("awc-source-list")?.replaceChildren(...required.map(renderSourceRow));
    byId("awc-optional-source-list")?.replaceChildren(...optional.map(renderSourceRow));
  }

  function renderHazards(snapshot) {
    const hazards = Array.isArray(snapshot?.activeHazards) ? snapshot.activeHazards : [];
    const card = byId("awc-hazard-card");
    const list = byId("awc-hazard-list");
    if (!card || !list) return;
    card.hidden = hazards.length === 0;
    list.replaceChildren();
    for (const hazard of hazards) {
      const item = document.createElement("li");
      const clearProgress = finite(hazard.clearStreak) && finite(hazard.clearCyclesRequired)
        ? `Entwarnung ${hazard.clearStreak} von ${hazard.clearCyclesRequired} frischen Zyklen.`
        : "Entwarnungsfortschritt noch nicht verfügbar.";
      item.textContent = `${hazard.reason || hazard.reasonCode || "Gefahr bleibt vorsorglich aktiv."} Zuletzt bestätigt ${formatTime(hazard.lastConfirmedAt)}. Mindesthaltezeit bis ${formatTime(hazard.holdUntil)}. ${clearProgress}`;
      list.append(item);
    }
    const ack = byId("awc-ack");
    if (ack) ack.disabled = hazards.length === 0 && !state.alert;
  }

  function readPreviousTrusted() {
    const raw = safeStorageGet(STORAGE_KEYS.trustedSnapshot);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  function compactTrustedSnapshot(snapshot) {
    const radar = normalizeRadar(snapshot);
    const warnings = Array.isArray(sourceById(snapshot, "DWD_CAP")?.payload?.active)
      ? sourceById(snapshot, "DWD_CAP").payload.active
      : [];
    return {
      snapshotId: snapshot.snapshotId,
      evaluationAt: snapshot.evaluationAt,
      riskState: snapshot.hardwareRisk?.state,
      distanceKm: radar.distanceKm,
      arrivalMinutes: radar.arrivalMinutes,
      warningIds: warnings.map((warning) => warning.identifier || warning.headline || warning.event),
      sourceStates: snapshot.sourceStates || {},
    };
  }

  function compareTrusted(previous, current) {
    if (!previous) return ["Erster vollständiger Live-Snapshot gespeichert. Der nächste Abruf kann dagegen verglichen werden."];
    const changes = [];
    if (previous.riskState !== current.riskState) {
      changes.push(`Live-Sicherheitszustand von ${previous.riskState || "unbekannt"} auf ${current.riskState || "unbekannt"} geändert.`);
    }
    if (finite(previous.distanceKm) && finite(current.distanceKm)) {
      const delta = Math.round((current.distanceKm - previous.distanceKm) * 10) / 10;
      if (Math.abs(delta) >= .5) {
        changes.push(delta < 0
          ? `Nächste Niederschlagsfläche ${Math.abs(delta)} km näher.`
          : `Nächste Niederschlagsfläche ${delta} km weiter entfernt.`);
      }
    }
    if (previous.arrivalMinutes !== current.arrivalMinutes) {
      if (current.arrivalMinutes === null) changes.push("Kein aktuelles Ankunftsfenster mehr erkannt.");
      else changes.push(`Neues mögliches Ankunftsfenster in etwa ${Math.round(current.arrivalMinutes)} Minuten.`);
    }
    const oldWarnings = new Set(previous.warningIds || []);
    const newWarnings = new Set(current.warningIds || []);
    const added = [...newWarnings].filter((id) => !oldWarnings.has(id)).length;
    const removed = [...oldWarnings].filter((id) => !newWarnings.has(id)).length;
    if (added) changes.push(`${added} amtliche Warnung${added === 1 ? "" : "en"} hinzugekommen.`);
    if (removed) changes.push(`${removed} amtliche Warnung${removed === 1 ? "" : "en"} aufgehoben oder ersetzt.`);
    for (const [sourceId, sourceState] of Object.entries(current.sourceStates || {})) {
      const old = previous.sourceStates?.[sourceId];
      if (old && old !== sourceState) {
        changes.push(`${SOURCE_LABELS[sourceId] || sourceId}: ${old} → ${sourceState}.`);
      }
    }
    return changes.length ? changes : ["Seit dem letzten vollständigen Abruf keine sicherheitsrelevante Veränderung."];
  }

  function renderChanges(snapshot) {
    const current = compactTrustedSnapshot(snapshot);
    const changes = snapshot?.hardwareRisk?.dataQuality === "COMPLETE"
      ? compareTrusted(state.previousTrusted, current)
      : ["Kein Vergleich gespeichert, solange die erforderlichen Live-Daten nicht vollständig sind."];
    const list = byId("awc-change-list");
    if (list) {
      list.replaceChildren(...changes.map((change) => {
        const item = document.createElement("li");
        item.textContent = change;
        return item;
      }));
    }
    if (snapshot?.hardwareRisk?.dataQuality === "COMPLETE") {
      state.previousTrusted = current;
      safeStorageSet(STORAGE_KEYS.trustedSnapshot, JSON.stringify(current));
    }
  }

  function renderAlarm() {
    const alert = state.alert;
    const enabled = state.alarmsEnabled;
    let copy = alert
      ? `${alert.riskState || "ALARM"}: ${alert.requiresAttention === false || alert.acknowledgedAt ? "quittiert" : "Aufmerksamkeit erforderlich"}.`
      : "Kein aktiver Alarm.";
    const permission = typeof Notification === "undefined" ? "nicht verfügbar" : Notification.permission;
    copy += ` Ton ${enabled ? "aktiv" : "nicht aktiviert"}; Browsermeldung ${permission}.`;
    text("awc-alarm-state", copy);
    text("awc-enable-alerts", enabled ? "Alarme aktiviert" : "Alarme aktivieren");
    const ack = byId("awc-ack");
    if (ack) ack.disabled = !alert && !(state.snapshot?.activeHazards || []).length;
  }

  function renderTechnical(snapshot) {
    text("awc-tech-snapshot", snapshot?.snapshotId || "–");
    text("awc-tech-algorithm", snapshot?.algorithmVersion || "–");
    text("awc-tech-config", snapshot?.configurationVersion || "–");
    text("awc-tech-time", formatTime(snapshot?.evaluationAt));
    text("awc-tech-reasons", (snapshot?.hardwareRisk?.reasonCodes || []).join(", ") || "keine");
  }

  function renderSnapshot(snapshot) {
    state.snapshot = snapshot;
    state.connection = "CONNECTED";
    state.forecast = deriveForecastState();
    renderHeader(snapshot);
    renderAxes(snapshot);
    const radar = renderRadar(snapshot);
    renderAction(snapshot, radar);
    renderEquipment(snapshot);
    renderWarnings(snapshot);
    renderSources(snapshot);
    renderHazards(snapshot);
    renderChanges(snapshot);
    renderAlarm();
    renderTechnical(snapshot);
    const live = livePresentation(snapshot);
    document.title = `${live.tone === "GREEN" ? "" : `${live.short} – `}Astro-Wolkencheck`;
  }

  function renderDisconnected(reason) {
    state.connection = "DISCONNECTED";
    const snapshot = state.snapshot
      ? {
          ...state.snapshot,
          hardwareRisk: {
            ...state.snapshot.hardwareRisk,
            state: "UNKNOWN",
            dataQuality: "INSUFFICIENT",
            reasonCodes: ["LOCAL_API_DISCONNECTED"],
            reasons: [reason || "Die lokale API ist nicht verbunden."],
          },
        }
      : {
          hardwareRisk: { state: "UNKNOWN", dataQuality: "INSUFFICIENT" },
          equipmentState: "UNKNOWN",
          sources: [],
        };
    renderHeader(snapshot);
    renderAxes(snapshot);
    const radar = normalizeRadar(snapshot);
    renderAction(snapshot, radar);
    const urgent = byId("awc-urgent-announcement");
    if (urgent) urgent.textContent = "Live-Verbindung unterbrochen. Sicherheitslage unbekannt.";
  }

  async function fetchJson(path, options = {}) {
    const response = await fetch(path, { cache: "no-store", ...options });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function ensureCsrf() {
    if (state.csrfToken) return state.csrfToken;
    const payload = await fetchJson(`${API}/security/csrf`);
    state.csrfToken = payload.token;
    return state.csrfToken;
  }

  async function writeApi(path, options = {}) {
    const token = await ensureCsrf();
    return fetchJson(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": token,
        ...(options.headers || {}),
      },
    });
  }

  async function refreshAll() {
    const button = byId("awc-refresh-live");
    if (button) {
      button.disabled = true;
      button.textContent = "Live-Daten werden aktualisiert …";
    }
    try {
      await writeApi(`${API}/runtime/refresh`, { method: "POST" });
    } finally {
      window.setTimeout(() => {
        if (button) {
          button.disabled = false;
          button.textContent = "Live-Daten aktualisieren";
        }
      }, 1200);
    }
  }

  async function acknowledge() {
    await writeApi(`${API}/alerts/acknowledge`, { method: "POST" });
    state.alert = null;
    renderAlarm();
  }

  function equipmentOptions() {
    return [
      ["NOT_DEPLOYED", "Noch nicht aufgebaut", "Bei Gefahr wird vom Aufbau abgeraten."],
      ["DEPLOYED_ATTENDED", "Aufgebaut und beaufsichtigt", "Bei Gefahr sofort prüfen und schützen."],
      ["DEPLOYED_UNATTENDED", "Aufgebaut und unbeaufsichtigt", "Gefahren benötigen besonders dringende Aufmerksamkeit."],
      ["UNKNOWN", "Unbekannt", "Nur verwenden, wenn der reale Zustand wirklich nicht feststeht."],
    ];
  }

  async function setEquipment(equipmentState) {
    await writeApi(`${API}/session`, {
      method: "PATCH",
      body: JSON.stringify({ equipment_state: equipmentState }),
    });
    byId("awc-equipment-dialog")?.close();
  }

  function openEquipmentDialog() {
    const dialog = byId("awc-equipment-dialog");
    const options = byId("awc-equipment-options");
    if (!dialog || !options) return;
    options.replaceChildren(...equipmentOptions().map(([value, label, help]) => {
      const button = document.createElement("button");
      button.className = "awc-equipment-option";
      button.type = "button";
      const strong = document.createElement("strong");
      strong.textContent = label;
      const span = document.createElement("span");
      span.textContent = help;
      button.append(strong, span);
      button.addEventListener("click", () => setEquipment(value).catch((error) => {
        console.error("Ausrüstungsstatus konnte nicht gespeichert werden", error);
      }));
      return button;
    }));
    dialog.showModal();
  }

  function ensureAudioContext() {
    if (!state.runtimeConfig.browserAudioEnabled) return null;
    const AudioCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtor) return null;
    if (!state.audioContext) state.audioContext = new AudioCtor();
    return state.audioContext;
  }

  function tone(frequency, start, duration) {
    const context = ensureAudioContext();
    if (!context) return;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "square";
    oscillator.frequency.setValueAtTime(frequency, start);
    gain.gain.setValueAtTime(.0001, start);
    gain.gain.exponentialRampToValueAtTime(.16, start + .02);
    gain.gain.exponentialRampToValueAtTime(.0001, start + duration);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start(start);
    oscillator.stop(start + duration + .03);
  }

  function playAlarm(riskState = "YELLOW") {
    if (!state.alarmsEnabled && riskState !== "TEST") return;
    const context = ensureAudioContext();
    if (!context) return;
    if (context.state === "suspended") context.resume();
    const start = context.currentTime + .03;
    if (riskState === "RED") {
      tone(760, start, .22);
      tone(520, start + .3, .22);
      tone(760, start + .6, .34);
    } else {
      tone(660, start, .18);
      tone(660, start + .28, .18);
    }
  }

  async function enableAlerts() {
    state.alarmsEnabled = true;
    safeStorageSet(STORAGE_KEYS.alerts, "1");
    const context = ensureAudioContext();
    if (context?.state === "suspended") await context.resume();
    if (
      state.runtimeConfig.browserNotificationsEnabled
      && typeof Notification !== "undefined"
      && Notification.permission === "default"
    ) {
      await Notification.requestPermission();
    }
    renderAlarm();
  }

  function testAlarm() {
    playAlarm("TEST");
    if (typeof Notification !== "undefined" && Notification.permission === "granted") {
      new Notification("Astro-Wolkencheck: Test", {
        body: "Testmeldung – es wurde keine echte Gefahr erzeugt.",
        tag: "awc-test",
      });
    }
  }

  function handleAlert(alert) {
    state.alert = alert || null;
    renderAlarm();
    if (!alert || !alert.requiresAttention || alert.eventId === state.lastAlertEventId) return;
    state.lastAlertEventId = alert.eventId;
    playAlarm(alert.riskState || "YELLOW");
    if (
      state.runtimeConfig.browserNotificationsEnabled
      && typeof Notification !== "undefined"
      && Notification.permission === "granted"
    ) {
      new Notification(
        alert.riskState === "RED" ? "Astro-Wolkencheck: ROT" : "Astro-Wolkencheck: GELB",
        {
          body: (alert.reasonCodes || []).join(", ") || "Neue sicherheitsrelevante Zustandsänderung.",
          tag: alert.eventId || alert.snapshotId,
          requireInteraction: alert.riskState === "RED",
        },
      );
    }
  }

  function connectEvents() {
    state.stream?.close();
    const stream = new EventSource(`${API}/events`);
    state.stream = stream;
    stream.addEventListener("open", () => {
      state.connection = "CONNECTED";
      renderHeader(state.snapshot);
    });
    stream.addEventListener("snapshot", (event) => {
      try {
        const envelope = JSON.parse(event.data);
        renderSnapshot(envelope.payload || envelope);
      } catch (error) {
        console.error("Snapshot-Ereignis konnte nicht gelesen werden", error);
      }
    });
    stream.addEventListener("source-state", () => {
      fetchJson(`${API}/safety`).then(renderSnapshot).catch(() => {});
    });
    stream.addEventListener("alert", (event) => {
      try {
        const envelope = JSON.parse(event.data);
        handleAlert(envelope.payload || envelope);
      } catch (error) {
        console.error("Alarm-Ereignis konnte nicht gelesen werden", error);
      }
    });
    stream.addEventListener("acknowledgement", () => {
      state.alert = null;
      renderAlarm();
    });
    stream.addEventListener("error", () => {
      renderDisconnected("Die lokale Ereignisverbindung ist unterbrochen.");
    });
  }

  function watchForecast() {
    const target = document.getElementById("overviewContent");
    if (!target) return;
    let queued = false;
    const update = () => {
      if (queued) return;
      queued = true;
      queueMicrotask(() => {
        queued = false;
        const next = deriveForecastState();
        if (
          next.title === state.forecast.title
          && next.detail === state.forecast.detail
          && next.confidence === state.forecast.confidence
        ) return;
        state.forecast = next;
        if (state.snapshot) {
          renderAxes(state.snapshot);
          renderAction(state.snapshot, normalizeRadar(state.snapshot));
          renderHeader(state.snapshot);
        }
      });
    };
    new MutationObserver(update).observe(target, { childList: true, subtree: true, characterData: true });
  }

  function startClock() {
    const tick = () => {
      text("awc-local-clock", `${new Intl.DateTimeFormat("de-DE", {
        timeZone: "Europe/Berlin",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }).format(new Date())} Uhr`);
      if (state.snapshot?.evaluationAt) {
        const age = Date.now() - new Date(state.snapshot.evaluationAt).getTime();
        if (age > MAX_SNAPSHOT_AGE_MS && state.connection !== "DISCONNECTED") {
          renderDisconnected("Der letzte Live-Snapshot ist älter als zwei Minuten.");
        } else {
          renderHeader(state.snapshot);
        }
      }
    };
    tick();
    window.setInterval(tick, 1000);
  }

  function bindEvents() {
    byId("awc-set-equipment")?.addEventListener("click", openEquipmentDialog);
    byId("awc-refresh-live")?.addEventListener("click", () => refreshAll().catch((error) => {
      console.error("Live-Aktualisierung fehlgeschlagen", error);
      renderDisconnected("Die Live-Aktualisierung ist fehlgeschlagen.");
    }));
    byId("awc-ack")?.addEventListener("click", () => acknowledge().catch((error) => {
      console.error("Alarm konnte nicht quittiert werden", error);
    }));
    byId("awc-enable-alerts")?.addEventListener("click", () => enableAlerts().catch((error) => {
      console.error("Alarme konnten nicht aktiviert werden", error);
    }));
    byId("awc-test-alarm")?.addEventListener("click", testAlarm);
  }

  async function loadInitialData() {
    try {
      const [runtimeConfig, snapshot, alerts] = await Promise.all([
        fetchJson("/runtime-config.json"),
        fetchJson(`${API}/safety`),
        fetchJson(`${API}/alerts`),
      ]);
      state.runtimeConfig = { ...state.runtimeConfig, ...runtimeConfig };
      state.alert = alerts.active || null;
      renderSnapshot(snapshot);
      renderAlarm();
      connectEvents();
    } catch (error) {
      console.error("Lokale Live-Daten konnten nicht geladen werden", error);
      renderDisconnected("Die lokale API konnte nicht geladen werden.");
    }
  }

  function init() {
    if (!prepareForecastShell()) return;
    state.previousTrusted = readPreviousTrusted();
    mountShell();
    bindEvents();
    watchForecast();
    startClock();
    loadInitialData();
  }

  window.AstroWolkencheckLiveDashboard = Object.freeze({
    normalizeRadar,
    radarArrivalCopy,
    radarMovementCopy,
    warningHardwareRelevance,
    dataConfidence,
    deriveForecastState,
    getState: () => state,
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
