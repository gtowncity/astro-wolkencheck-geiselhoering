(() => {
  "use strict";

  const API = "/api/v1";
  const DASHBOARD = "AstroWolkencheckLiveDashboard";
  const SOURCE_LABELS = Object.freeze({
    DWD_RV: "DWD-Regenradar",
    DWD_CAP: "Amtliche DWD-Warnungen",
    LOCAL_PERSISTENCE: "Lokaler Sicherheitsspeicher",
    DWD_WN: "DWD-Niederschlagsanalyse",
    RAIN_SENSOR: "Regensensor am Teleskop",
  });
  let requestedSnapshotId = null;
  let requestInFlight = false;
  let lastSummary = null;

  function finite(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function number(value, unit = "") {
    if (!finite(value)) return "nicht verfügbar";
    const formatted = new Intl.NumberFormat("de-DE", {
      maximumFractionDigits: 1,
    }).format(Math.abs(value));
    return `${formatted}${unit ? ` ${unit}` : ""}`;
  }

  function warningName(warning) {
    return warning?.headline || warning?.event || warning?.id || "Amtliche Warnung";
  }

  function summaryLines(summary) {
    if (!summary?.hasPrevious) {
      return [
        "Erster vollständiger Live-Snapshot ist serverseitig gespeichert. " +
          "Der nächste vollständige Abruf wird damit verglichen.",
      ];
    }

    const lines = [];
    if (summary.riskStateChanged) {
      lines.push(
        `Live-Sicherheitszustand: ${summary.previousRiskState || "unbekannt"} → ` +
          `${summary.currentRiskState || "unbekannt"}.`,
      );
    }
    if (summary.equipmentStateChanged) {
      lines.push("Der gespeicherte Ausrüstungsstatus wurde geändert.");
    }

    const delta = summary.radarDistanceDeltaKm;
    if (finite(delta) && Math.abs(delta) >= 0.5) {
      lines.push(
        delta < 0
          ? `Nächste Niederschlagsfläche ${number(delta, "km")} näher.`
          : `Nächste Niederschlagsfläche ${number(delta, "km")} weiter entfernt.`,
      );
    }

    if (summary.arrivalChanged) {
      lines.push(
        finite(summary.currentArrivalMinutes)
          ? `Neues Ankunftsfenster in etwa ${number(
              summary.currentArrivalMinutes,
              "Minuten",
            )}.`
          : "Das zuvor erkannte Ankunftsfenster ist nicht mehr vorhanden.",
      );
    }
    if (summary.rainNowChanged) {
      lines.push(
        summary.currentRainNow
          ? "Radar erkennt jetzt Niederschlag am Standort."
          : "Radar erkennt am Standort aktuell keinen Niederschlag mehr.",
      );
    }

    for (const warning of summary.warningsAdded || []) {
      lines.push(`Neu: ${warningName(warning)}.`);
    }
    for (const warning of summary.warningsRemoved || []) {
      lines.push(`Aufgehoben oder ersetzt: ${warningName(warning)}.`);
    }
    for (const change of summary.sourceStateChanges || []) {
      const label = SOURCE_LABELS[change.sourceId] || change.sourceId;
      lines.push(
        `${label}: ${change.previous || "nicht verfügbar"} → ` +
          `${change.current || "nicht verfügbar"}.`,
      );
    }

    return lines.length
      ? lines
      : ["Seit dem letzten vollständigen Abruf keine sicherheitsrelevante Veränderung."];
  }

  function introCopy(summary) {
    if (!summary?.hasPrevious) {
      return (
        "Erster vollständiger Snapshot gespeichert; der Vergleich folgt nach " +
        "dem nächsten vollständigen Abruf."
      );
    }
    const count = finite(summary.meaningfulChangeCount)
      ? summary.meaningfulChangeCount
      : 0;
    if (count === 0) {
      return (
        "Keine sicherheitsrelevante Veränderung seit dem vorherigen " +
        "vollständigen Snapshot."
      );
    }
    return count === 1
      ? "1 sicherheitsrelevante Änderung seit dem vorherigen vollständigen Snapshot."
      : `${count} sicherheitsrelevante Änderungen seit dem vorherigen vollständigen Snapshot.`;
  }

  function render(summary) {
    const list = document.getElementById("awc-change-list");
    if (!list) return;
    const intro =
      document.getElementById("awc-change-summary") ||
      document.querySelector(".awc-change-card .awc-card-head p");
    if (intro) intro.textContent = introCopy(summary);

    const lines = summaryLines(summary);
    const currentLines = [...list.children].map((item) => item.textContent || "");
    if (
      list.dataset.source === "SERVER_HISTORY" &&
      currentLines.length === lines.length &&
      currentLines.every((copy, index) => copy === lines[index])
    ) {
      return;
    }
    list.replaceChildren(
      ...lines.map((copy) => {
        const item = document.createElement("li");
        item.textContent = copy;
        return item;
      }),
    );
    list.dataset.source = "SERVER_HISTORY";
  }

  async function refresh() {
    const api = window[DASHBOARD];
    const snapshot = api?.getState?.().snapshot;
    if (!snapshot || requestInFlight) return;
    if (snapshot.hardwareRisk?.dataQuality !== "COMPLETE") return;
    if (snapshot.snapshotId === requestedSnapshotId && lastSummary) {
      render(lastSummary);
      return;
    }

    requestInFlight = true;
    try {
      const response = await fetch(`${API}/changes`, { cache: "no-store" });
      if (!response.ok) return;
      const summary = await response.json();
      if (
        summary.currentSnapshotId &&
        summary.currentSnapshotId !== snapshot.snapshotId
      ) {
        window.setTimeout(refresh, 500);
        return;
      }
      requestedSnapshotId = snapshot.snapshotId;
      lastSummary = summary;
      render(summary);
    } catch (error) {
      console.warn("Serverseitiger Snapshotvergleich ist vorübergehend nicht verfügbar", error);
    } finally {
      requestInFlight = false;
    }
  }

  window.AstroWolkencheckLiveChanges = Object.freeze({
    render,
    refresh,
    summaryLines,
    introCopy,
  });

  function init() {
    const root = document.getElementById("live-dashboard-root");
    if (!root) {
      window.setTimeout(init, 100);
      return;
    }
    new MutationObserver(refresh).observe(root, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    refresh();
    window.setInterval(refresh, 3000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
