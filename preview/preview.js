(() => {
  "use strict";

  const banner = document.getElementById("preview-banner");
  const controls = document.getElementById("preview-controls");

  function placePreviewChrome() {
    const dashboard = document.getElementById("live-dashboard-root");
    if (!dashboard) return false;

    if (banner && banner.nextElementSibling !== dashboard) {
      document.body.insertBefore(banner, dashboard);
    }
    if (controls && dashboard.nextElementSibling !== controls) {
      dashboard.insertAdjacentElement("afterend", controls);
    }
    return true;
  }

  function waitForDashboard() {
    if (placePreviewChrome()) return;
    window.setTimeout(waitForDashboard, 50);
  }

  for (const button of document.querySelectorAll("[data-preview-state]")) {
    button.addEventListener("click", () => {
      const state = button.getAttribute("data-preview-state");
      if (!state) return;
      window.__awcPreviewSetState?.(state);
      for (const item of document.querySelectorAll("[data-preview-state]")) {
        item.setAttribute("aria-pressed", String(item === button));
      }
    });
  }

  document.getElementById("preview-disconnect")?.addEventListener("click", () => {
    window.__awcPreviewDisconnect?.();
  });

  document
    .querySelector("[data-preview-state='GREEN']")
    ?.setAttribute("aria-pressed", "true");

  waitForDashboard();
})();
