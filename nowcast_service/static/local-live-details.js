(() => {
  "use strict";

  const DASHBOARD = "AstroWolkencheckLiveDashboard";
  const SOURCE_LABELS = Object.freeze({
    DWD_RV: "DWD-Regenradar",
    DWD_CAP: "Amtliche DWD-Warnungen",
    LOCAL_PERSISTENCE: "Lokaler Sicherheitsspeicher",
    DWD_WN: "DWD-Niederschlagsanalyse",
    RAIN_SENSOR: "Regensensor am Teleskop",
  });
  const URGENCY = Object.freeze({
    Immediate: "sofort",
    Expected: "erwartet",
    Future: "zukünftig",
    Past: "vergangen",
    Unknown: "nicht angegeben",
  });
  const CERTAINTY = Object.freeze({
    Observed: "beobachtet",
    Likely: "wahrscheinlich",
    Possible: "möglich",
    Unlikely: "unwahrscheinlich",
    Unknown: "nicht angegeben",
  });
  let lastSignature = null;
  let announcementObserver = null;
  let lastAnnouncementTone = null;
  let lastAnnouncementConnection = null;

  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const time = (value, empty = "nicht angegeben") => {
    if (!value) return empty;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return empty;
    return new Intl.DateTimeFormat("de-DE", {
      dateStyle: "short",
      timeStyle: "short",
    }).format(date);
  };

  function sourceById(snapshot, sourceId) {
    return (snapshot?.sources || []).find((item) => item.sourceId === sourceId) || null;
  }

  function fact(label, value) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = value;
    row.append(term, detail);
    return row;
  }

  function detailSection(title, copy) {
    const section = document.createElement("section");
    const heading = document.createElement("h4");
    const paragraph = document.createElement("p");
    heading.textContent = title;
    paragraph.textContent = copy;
    section.append(heading, paragraph);
    return section;
  }

  function renderWarnings(snapshot, api) {
    const source = sourceById(snapshot, "DWD_CAP");
    const warnings = Array.isArray(source?.payload?.active) ? source.payload.active : [];
    const articles = [...document.querySelectorAll("#awc-warning-list .awc-warning")];
    const hardwareCount = warnings.filter((warning) =>
      ["RED", "YELLOW"].includes(api.warningHardwareRelevance(warning).tone)
    ).length;
    const count = document.getElementById("awc-warning-count");
    if (count) {
      count.textContent = `${warnings.length} aktiv · ${hardwareCount} Hardware-Veto${hardwareCount === 1 ? "" : "s"}`;
    }

    articles.forEach((article, index) => {
      const warning = warnings[index];
      if (!warning) return;
      const relevance = api.warningHardwareRelevance(warning);
      article.querySelector(".awc-warning-facts")?.remove();
      const facts = document.createElement("dl");
      facts.className = "awc-warning-facts";
      const rows = [
        ["Ereignis", warning.event || "nicht angegeben"],
        [
          "Dringlichkeit",
          URGENCY[warning.urgency] || warning.urgency || "nicht angegeben",
        ],
        [
          "Sicherheit",
          CERTAINTY[warning.certainty] || warning.certainty || "nicht angegeben",
        ],
        ["Gültig ab", time(warning.effective || warning.sent)],
        ["Erwarteter Beginn", time(warning.onset)],
        [
          "Ende",
          time(
            warning.expires,
            "keine feste Ablaufzeit im aktuellen vollständigen DWD-Archiv",
          ),
        ],
        [
          "Quelle",
          warning.senderName || warning.sender || "Deutscher Wetterdienst (DWD)",
        ],
      ];
      facts.append(...rows.map(([label, value]) => fact(label, value)));

      const details = article.querySelector("details");
      if (details) {
        const summary =
          details.querySelector("summary") || document.createElement("summary");
        summary.textContent = "Einordnung, Beschreibung und Verhalten";
        details.replaceChildren(
          summary,
          facts,
          detailSection("Hardware-Einordnung", relevance.description),
          detailSection(
            "Amtliche Beschreibung",
            warning.description || "Keine zusätzliche Beschreibung geliefert.",
          ),
          detailSection(
            "DWD-Verhaltensempfehlung",
            warning.instruction || "Keine zusätzliche Handlungsanweisung geliefert.",
          ),
        );
      }
      article.setAttribute(
        "aria-label",
        `${warning.headline || warning.event || "Amtliche Warnung"}. ${relevance.label}.`,
      );
    });
  }

  function renderHazards(snapshot) {
    const hazards = Array.isArray(snapshot?.activeHazards)
      ? snapshot.activeHazards
      : [];
    const list = document.getElementById("awc-hazard-list");
    if (!list || !hazards.length) return;
    list.replaceChildren(
      ...hazards.map((hazard) => {
        const item = document.createElement("li");
        item.className = "awc-hazard-entry";
        const heading = document.createElement("strong");
        heading.textContent =
          hazard.reason || hazard.reasonCode || "Gefahr bleibt vorsorglich aktiv";
        const source = document.createElement("p");
        source.textContent = `Quelle: ${SOURCE_LABELS[hazard.source] || hazard.source || "nicht angegeben"}`;
        const facts = document.createElement("dl");
        facts.append(
          fact("Zuletzt bestätigt", time(hazard.lastConfirmedAt)),
          fact("Mindesthaltezeit", time(hazard.holdUntil)),
          fact(
            "Entwarnung",
            finite(hazard.clearStreak) && finite(hazard.clearCyclesRequired)
              ? `${hazard.clearStreak} von ${hazard.clearCyclesRequired} frischen Zyklen`
              : "Fortschritt noch nicht verfügbar",
          ),
          fact(
            "Quittiert",
            hazard.acknowledgedAt ? time(hazard.acknowledgedAt) : "nein",
          ),
        );
        const note = document.createElement("small");
        note.textContent =
          hazard.clearCondition ||
          "Die Gefahr endet erst nach den festgelegten frischen Entwarnungszyklen.";
        item.append(heading, source, facts, note);
        return item;
      }),
    );
  }

  function capability(label, value, tone) {
    const row = document.createElement("div");
    row.dataset.tone = tone;
    const name = document.createElement("span");
    const status = document.createElement("strong");
    name.textContent = label;
    status.textContent = value;
    row.append(name, status);
    return row;
  }

  function ensureNotificationTest(buttonRow) {
    let button = document.getElementById("awc-test-notification");
    if (button) return button;
    button = document.createElement("button");
    button.id = "awc-test-notification";
    button.type = "button";
    button.textContent = "Browsermeldung testen";
    button.addEventListener("click", async () => {
      const status = document.getElementById("awc-alarm-state");
      if (typeof Notification === "undefined") {
        if (status) {
          status.textContent =
            "Browsermeldungen werden von diesem Browser nicht unterstützt.";
        }
        return;
      }
      let permission = Notification.permission;
      if (permission === "default") {
        permission = await Notification.requestPermission();
      }
      if (permission !== "granted") {
        if (status) {
          status.textContent =
            "Browsermeldung ist nicht erlaubt. Bitte Browserberechtigung prüfen.";
        }
        return;
      }
      new Notification("Astro-Wolkencheck: Test", {
        body: "Die Browserbenachrichtigung funktioniert.",
        tag: "awc-notification-test",
      });
      if (status) {
        status.textContent =
          "Test-Browsermeldung wurde ausgelöst. Es wurde keine Gefahr erzeugt.";
      }
    });
    const ack = document.getElementById("awc-ack");
    buttonRow.insertBefore(button, ack || null);
    return button;
  }

  function renderAlarm(snapshot) {
    const card = document.querySelector(".awc-alarm-card");
    const stateNode = document.getElementById("awc-alarm-state");
    const buttonRow = card?.querySelector(".awc-button-row");
    if (!card || !stateNode || !buttonRow) return;
    let capabilities = document.getElementById("awc-alarm-capabilities");
    if (!capabilities) {
      capabilities = document.createElement("div");
      capabilities.id = "awc-alarm-capabilities";
      capabilities.className = "awc-alarm-capabilities";
      stateNode.insertAdjacentElement("afterend", capabilities);
    }
    const browserPermission =
      typeof Notification === "undefined" ? "nicht verfügbar" : Notification.permission;
    const api = window[DASHBOARD];
    const enabled = Boolean(api?.getState?.().alarmsEnabled);
    capabilities.replaceChildren(
      capability(
        "Akustischer Alarm",
        enabled ? "aktiv" : "nicht aktiviert",
        enabled ? "GREEN" : "YELLOW",
      ),
      capability(
        "Browsermeldung",
        browserPermission === "granted"
          ? "erlaubt"
          : browserPermission === "denied"
            ? "blockiert"
            : browserPermission,
        browserPermission === "granted"
          ? "GREEN"
          : browserPermission === "denied"
            ? "RED"
            : "YELLOW",
      ),
      capability(
        "Aktiver Gefahren-Latch",
        snapshot?.activeHazards?.length ? "ja" : "nein",
        snapshot?.activeHazards?.length ? "RED" : "GREEN",
      ),
    );
    const enable = document.getElementById("awc-enable-alerts");
    const test = document.getElementById("awc-test-alarm");
    if (enable) {
      enable.textContent = enabled
        ? "Alarmierung aktiviert"
        : "Alarmierung einrichten";
    }
    if (test) test.textContent = "Alarmton testen";
    ensureNotificationTest(buttonRow);
    let note = document.getElementById("awc-ack-note");
    if (!note) {
      note = document.createElement("small");
      note.id = "awc-ack-note";
      note.className = "awc-ack-note";
      note.textContent =
        "Quittieren beendet Ton und Browseralarm, aber niemals die erkannte " +
        "Gefahr oder deren Haltezeit.";
      buttonRow.insertAdjacentElement("afterend", note);
    }
  }

  function announce(node, copy) {
    if (!node || !copy || node.textContent === copy) return;
    node.textContent = "";
    window.requestAnimationFrame(() => {
      node.textContent = copy;
    });
  }

  function inspectAnnouncements() {
    const axis = document.getElementById("awc-live-axis");
    const pill = document.getElementById("awc-api-pill");
    const urgent = document.getElementById("awc-urgent-announcement");
    const polite = document.getElementById("awc-polite-announcement");
    if (!axis || !pill || !urgent || !polite) return;

    const tone = axis.dataset.tone || "UNKNOWN";
    const connection = pill.dataset.connection || "CONNECTING";
    const toneChanged =
      lastAnnouncementTone !== null && tone !== lastAnnouncementTone;
    const connectionChanged =
      lastAnnouncementConnection !== null &&
      connection !== lastAnnouncementConnection;

    if (connectionChanged && connection === "DISCONNECTED") {
      announce(
        urgent,
        "Live-Verbindung unterbrochen. Sicherheitslage unbekannt.",
      );
    } else if (connectionChanged && connection === "CONNECTED") {
      announce(polite, "Lokale Live-Verbindung wiederhergestellt.");
    }

    if (toneChanged) {
      if (tone === "RED") {
        announce(
          urgent,
          "Rot. Unmittelbare Gefahr erkannt. Ausrüstung sofort schützen.",
        );
      } else if (tone === "UNKNOWN" && connection !== "DISCONNECTED") {
        announce(
          urgent,
          "Unbekannt. Live-Sicherheitslage nicht zuverlässig beurteilbar.",
        );
      } else if (tone === "YELLOW") {
        announce(
          polite,
          "Gelb. Erhöhte Aufmerksamkeit. Bedingungen erneut prüfen.",
        );
      } else if (tone === "GREEN") {
        announce(polite, "Grün. Kein aktuelles Live-Wetter-Veto erkannt.");
      }
    }

    lastAnnouncementTone = tone;
    lastAnnouncementConnection = connection;
  }

  function configureAnnouncements(root) {
    if (announcementObserver) return;
    const urgent = document.getElementById("awc-urgent-announcement");
    if (!urgent) return;
    urgent.setAttribute("role", "alert");
    urgent.setAttribute("aria-atomic", "true");

    let polite = document.getElementById("awc-polite-announcement");
    if (!polite) {
      polite = document.createElement("span");
      polite.id = "awc-polite-announcement";
      polite.className = "sr-only";
      polite.setAttribute("role", "status");
      polite.setAttribute("aria-live", "polite");
      polite.setAttribute("aria-atomic", "true");
      urgent.insertAdjacentElement("afterend", polite);
    }
    document.getElementById("awc-action-card")?.setAttribute("aria-live", "off");

    inspectAnnouncements();
    announcementObserver = new MutationObserver(inspectAnnouncements);
    announcementObserver.observe(root, {
      attributes: true,
      attributeFilter: ["data-tone", "data-connection"],
      subtree: true,
    });
  }

  function render() {
    const api = window[DASHBOARD];
    const snapshot = api?.getState?.().snapshot;
    if (!api || !snapshot) return;
    const warnings = sourceById(snapshot, "DWD_CAP")?.payload?.active || [];
    const signature = JSON.stringify({
      snapshotId: snapshot.snapshotId,
      warnings: warnings.map((warning) => [
        warning.identifier,
        warning.urgency,
        warning.certainty,
      ]),
      hazards: (snapshot.activeHazards || []).map((hazard) => [
        hazard.hazardKey,
        hazard.clearStreak,
        hazard.acknowledgedAt,
      ]),
      permission:
        typeof Notification === "undefined"
          ? "unsupported"
          : Notification.permission,
      alarmsEnabled: api.getState().alarmsEnabled,
    });
    if (signature === lastSignature) return;
    lastSignature = signature;
    renderWarnings(snapshot, api);
    renderHazards(snapshot);
    renderAlarm(snapshot);
  }

  function init() {
    const root = document.getElementById("live-dashboard-root");
    if (!root) {
      window.setTimeout(init, 100);
      return;
    }
    configureAnnouncements(root);
    new MutationObserver(render).observe(root, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    render();
    window.setInterval(render, 1500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
