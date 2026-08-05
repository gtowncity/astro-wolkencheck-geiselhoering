(() => {
  "use strict";
  const banner = document.createElement("section");
  banner.setAttribute("role", "status");
  banner.setAttribute("aria-live", "polite");
  banner.style.cssText = "box-sizing:border-box;padding:1rem;border:4px solid currentColor;background:#303238;color:#f5f7fa;font:700 1rem system-ui,sans-serif";
  banner.innerHTML = "<strong>LIVE-ÜBERWACHUNG NICHT VERBUNDEN</strong><p>Radar-, Warnungs- und Sensorüberwachung ist nur in der lokal gestarteten Version verfügbar. Die öffentliche Seite zeigt niemals einen Live-GRÜN-Zustand.</p>";
  document.body.insertBefore(banner, document.body.firstChild);
})();
