(() => {
  "use strict";
  const root = document.createElement("section");
  root.id = "awc-live-panel";
  root.setAttribute("aria-live", "polite");
  root.innerHTML = `<h2>LIVE-HARDWARE-RISIKO</h2><p id="awc-state">UNBEKANNT</p><p id="awc-action">Nicht auf diesen Zustand verlassen.</p><p id="awc-time"></p><button id="awc-refresh">Alle Quellen aktualisieren</button><button id="awc-ack">Alarm quittieren</button><pre id="awc-sources"></pre>`;
  document.body.insertBefore(root, document.body.firstChild);
  let csrf = null;
  async function token() {
    if (!csrf) csrf = (await fetch("/api/v1/security/csrf", {cache:"no-store"}).then(r => r.json())).token;
    return csrf;
  }
  function render(data) {
    const risk = data.hardwareRisk || {};
    root.dataset.state = risk.state || "UNKNOWN";
    document.getElementById("awc-state").textContent = risk.state || "UNKNOWN";
    document.getElementById("awc-action").textContent = risk.action || "UNKNOWN_DO_NOT_RELY";
    document.getElementById("awc-time").textContent = `Letzte Bewertung: ${data.evaluationAt || "unbekannt"}`;
    document.getElementById("awc-sources").textContent = JSON.stringify(data.sourceStates || {}, null, 2);
    document.title = (["RED", "YELLOW"].includes(risk.state) ? `${risk.state} – ` : "") + "Astro-Wolkencheck";
    if (risk.state === "RED") {
      try { new Audio("data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=").play(); } catch (_) {}
    }
  }
  async function load() {
    try {
      const response = await fetch("/api/v1/safety", {cache:"no-store"});
      if (!response.ok) throw new Error("api");
      render(await response.json());
    } catch (_) {
      render({hardwareRisk:{state:"UNKNOWN",action:"UNKNOWN_DO_NOT_RELY"},evaluationAt:new Date().toISOString(),sourceStates:{LOCAL_API:"DISCONNECTED"}});
    }
  }
  async function post(path) {
    await fetch(path, {method:"POST",headers:{"X-CSRF-Token":await token()},credentials:"same-origin"});
    await load();
  }
  document.getElementById("awc-refresh").addEventListener("click", () => post("/api/v1/runtime/refresh"));
  document.getElementById("awc-ack").addEventListener("click", () => post("/api/v1/alerts/acknowledge"));
  const stream = new EventSource("/api/v1/events");
  stream.addEventListener("snapshot", event => render(JSON.parse(event.data).payload));
  stream.onerror = load;
  load();
})();
