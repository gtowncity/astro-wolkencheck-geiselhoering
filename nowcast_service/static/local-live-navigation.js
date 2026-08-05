(() => {
  "use strict";

  const TAB_LABELS = Object.freeze({
    nowcast: "JETZT",
    overview: "NACHT PLANEN",
    hours: "STUNDEN",
    data: "DATEN & DIAGNOSE",
  });
  let initialized = false;

  function byId(id) {
    return document.getElementById(id);
  }

  function createExternalSources() {
    const details = document.createElement("details");
    details.className = "awc-external-sources";
    const summary = document.createElement("summary");
    summary.textContent = "Weitere offizielle Quellen für die Sichtprüfung";
    const copy = document.createElement("p");
    copy.textContent =
      "Die lokale Live-Entscheidung ersetzt nicht den Blick auf Himmel, Horizont und Ausrüstung.";
    const links = document.createElement("div");
    links.className = "awc-external-links";
    for (const item of [
      ["DWD-Warnlage", "https://www.dwd.de/DE/wetter/warnungen_gemeinden/warnWetter_node.html"],
      ["DWD-Radarfilm", "https://www.dwd.de/DE/leistungen/radarbild_film/radarbild_film.html"],
      ["Satellitenbild", "https://www.sat24.com/de-de/country/de"],
      ["Blitzortung", "https://www.blitzortung.org/de/live_lightning_maps.php"],
    ]) {
      const link = document.createElement("a");
      link.href = item[1];
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = item[0];
      links.append(link);
    }
    details.append(summary, copy, links);
    return details;
  }

  function activateTab(tabId) {
    for (const button of document.querySelectorAll(".tab-button[data-tab]")) {
      const active = button.dataset.tab === tabId;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    }
    for (const panel of document.querySelectorAll(".tab-panel")) {
      const active = panel.id === tabId;
      panel.classList.toggle("active", active);
      panel.hidden = !active;
    }
    const select = byId("mobileTabSelect");
    if (select && [...select.options].some((option) => option.value === tabId)) {
      select.value = tabId;
    }
    try {
      window.sessionStorage.setItem("awc-active-tab", tabId);
    } catch {
      // Tab persistence is optional.
    }
  }

  function configureNavigation() {
    const root = byId("live-dashboard-root");
    const tabsShell = document.querySelector(".tabs-shell");
    const tabs = tabsShell?.querySelector(".tabs");
    const nowPanel = byId("nowcast");
    const overviewPanel = byId("overview");
    const windowsPanel = byId("windows");
    if (!root || !tabsShell || !tabs || !nowPanel || !overviewPanel || !windowsPanel) {
      return false;
    }

    const buttons = new Map(
      [...tabs.querySelectorAll(".tab-button[data-tab]")].map((button) => [
        button.dataset.tab,
        button,
      ])
    );
    const nowButton = buttons.get("nowcast");
    const overviewButton = buttons.get("overview");
    const hoursButton = buttons.get("hours");
    const dataButton = buttons.get("data");
    const windowsButton = buttons.get("windows");
    if (!nowButton || !overviewButton || !hoursButton || !dataButton || !windowsButton) {
      return false;
    }

    for (const [tabId, label] of Object.entries(TAB_LABELS)) {
      const button = buttons.get(tabId);
      if (button) button.textContent = label;
    }
    tabs.replaceChildren(nowButton, overviewButton, hoursButton, dataButton);
    windowsButton.remove();

    const select = byId("mobileTabSelect");
    if (select) {
      for (const option of [...select.options]) {
        if (option.value === "windows") {
          option.remove();
        } else if (TAB_LABELS[option.value]) {
          option.textContent = TAB_LABELS[option.value];
          option.hidden = false;
        }
      }
    }

    if (!nowPanel.dataset.awcUnified) {
      nowPanel.replaceChildren(root, createExternalSources());
      nowPanel.dataset.awcUnified = "true";
    } else if (root.parentElement !== nowPanel) {
      nowPanel.prepend(root);
    }

    const planningPanel = windowsPanel.querySelector(":scope > .panel");
    const overviewContent = byId("overviewContent");
    if (planningPanel && overviewContent && planningPanel.parentElement !== overviewPanel) {
      planningPanel.classList.add("awc-planning-windows");
      overviewContent.insertAdjacentElement("afterend", planningPanel);
    }
    windowsPanel.hidden = true;
    windowsPanel.classList.remove("active");

    if (!initialized) {
      for (const button of [nowButton, overviewButton, hoursButton, dataButton]) {
        button.addEventListener("click", () => activateTab(button.dataset.tab));
      }
      select?.addEventListener("change", () => activateTab(select.value));
      initialized = true;
    }

    let initialTab = "nowcast";
    try {
      const stored = window.sessionStorage.getItem("awc-active-tab");
      if (stored && TAB_LABELS[stored]) initialTab = stored;
    } catch {
      // The first tab remains JETZT when session storage is unavailable.
    }
    const currentlyActive = document.querySelector(".tab-button.active")?.dataset.tab;
    activateTab(TAB_LABELS[currentlyActive] ? currentlyActive : initialTab);
    return true;
  }

  function init() {
    if (configureNavigation()) return;
    window.setTimeout(init, 100);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
