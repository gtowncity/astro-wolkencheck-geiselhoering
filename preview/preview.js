(() => {
  "use strict";

  const panel = document.getElementById("awc-live-panel");
  const banner = document.getElementById("preview-banner");
  const controls = document.getElementById("preview-controls");
  const diagnostics = document.querySelector("#awc-live-panel a[href='/api/v1/diagnostics']");

  if (panel && banner) {
    document.body.insertBefore(banner, panel);
  }
  if (panel && controls) {
    panel.insertAdjacentElement("afterend", controls);
  }

  diagnostics?.addEventListener("click", (event) => {
    event.preventDefault();
    window.alert("Die Diagnoseansicht ist in dieser statischen Vorschau deaktiviert.");
  });

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

  document.querySelector("[data-preview-state='GREEN']")?.setAttribute("aria-pressed", "true");
})();
