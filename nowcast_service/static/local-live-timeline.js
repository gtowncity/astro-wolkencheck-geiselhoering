(() => {
  "use strict";

  const DASHBOARD = "AstroWolkencheckLiveDashboard";
  let lastSignature = null;

  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
  const formatNumber = (value, unit = "", digits = 0) => {
    if (!finite(value)) return "nicht verfügbar";
    const text = new Intl.NumberFormat("de-DE", { maximumFractionDigits: digits }).format(value);
    return `${text}${unit ? ` ${unit}` : ""}`;
  };

  function ensureChart(track, points) {
    let svg = track.querySelector(".awc-timeline-chart");
    if (!svg) {
      svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "awc-timeline-chart");
      svg.setAttribute("viewBox", "0 0 100 100");
      svg.setAttribute("preserveAspectRatio", "none");
      svg.setAttribute("aria-hidden", "true");
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.id = "awc-timeline-path";
      svg.append(path);
      track.insertBefore(svg, points);
    }
    return svg.querySelector("path");
  }

  function ensureDetail(track) {
    let detail = document.getElementById("awc-timeline-detail");
    if (!detail) {
      detail = document.createElement("p");
      detail.id = "awc-timeline-detail";
      detail.className = "awc-timeline-detail";
      track.parentElement?.append(detail);
    }
    return detail;
  }

  function buildPath(frames, horizon) {
    const parts = [];
    let open = false;
    for (const frame of frames) {
      const lead = finite(frame.leadMinutes) ? frame.leadMinutes : 0;
      const distance = frame.nearestDistanceKm ?? frame.nearestPrecipitationDistanceKm;
      if (!finite(distance)) {
        open = false;
        continue;
      }
      const x = clamp(lead / horizon * 100, 0, 100);
      const y = 88 - clamp(distance, 0, 100) / 100 * 76;
      parts.push(`${open ? "L" : "M"} ${x.toFixed(2)} ${y.toFixed(2)}`);
      open = true;
    }
    return parts.join(" ");
  }

  function sampleIndexes(length) {
    const indexes = new Set([0, Math.max(0, length - 1)]);
    for (let index = 0; index < length; index += 3) indexes.add(index);
    return [...indexes].sort((left, right) => left - right);
  }

  function render() {
    const api = window[DASHBOARD];
    const snapshot = api?.getState?.().snapshot;
    if (!api || !snapshot) return;
    const radar = api.normalizeRadar(snapshot);
    const frames = [...(radar.timeline || [])].sort(
      (left, right) => (left.leadMinutes ?? 0) - (right.leadMinutes ?? 0),
    );
    if (!frames.length) return;

    const signature = `${snapshot.snapshotId}:${frames.length}:${frames.at(-1)?.nearestDistanceKm ?? "none"}`;
    if (signature === lastSignature) return;
    lastSignature = signature;

    const track = document.querySelector(".awc-timeline-track");
    const points = document.getElementById("awc-timeline-points");
    const summary = document.getElementById("awc-timeline-summary");
    if (!track || !points || !summary) return;

    const horizon = Math.max(1, radar.horizonMinutes || frames.at(-1)?.leadMinutes || 120);
    const path = ensureChart(track, points);
    path?.setAttribute("d", buildPath(frames, horizon));
    points.replaceChildren();

    for (const index of sampleIndexes(frames.length)) {
      const frame = frames[index];
      const lead = finite(frame.leadMinutes) ? frame.leadMinutes : 0;
      const distance = frame.nearestDistanceKm ?? frame.nearestPrecipitationDistanceKm;
      if (!finite(distance)) continue;
      const point = document.createElement("i");
      point.dataset.rain = String(Boolean(frame.rainAtSite));
      point.dataset.coverage = String(frame.coverageSufficient !== false);
      point.style.left = `${clamp(lead / horizon * 100, 1.5, 98.5)}%`;
      point.style.top = `${88 - clamp(distance, 0, 100) / 100 * 76}%`;
      const ring10 = Array.isArray(frame.rings)
        ? frame.rings.find((ring) => Math.round(ring.radiusKm) === 10)
        : null;
      const ringText = ring10
        ? ` · 10-km-Ring: ${ring10.wetPixelCount ?? 0} nasse Pixel`
        : "";
      point.title = `+${lead} Min.: ${formatNumber(distance, "km")} ${frame.nearestDirection || ""}${ringText}`.trim();
      points.append(point);
    }

    const distances = frames
      .map((frame) => frame.nearestDistanceKm ?? frame.nearestPrecipitationDistanceKm)
      .filter(finite);
    const earliestRain = frames.find((frame) => frame.rainAtSite === true)?.leadMinutes;
    let summaryText = "Keine relevante Fläche im Verlauf";
    let detailText = `${frames.length} validierte Radarframes · Horizont ${horizon} Minuten.`;
    if (distances.length) {
      const first = distances[0];
      const last = distances.at(-1);
      const delta = last - first;
      if (Math.abs(delta) < 2) {
        summaryText = "Entfernung bleibt nahezu gleich";
        detailText += ` Die nächste Fläche bleibt ungefähr ${formatNumber(first, "km")} entfernt.`;
      } else if (delta < 0) {
        summaryText = `Fläche nähert sich um ${formatNumber(Math.abs(delta), "km")}`;
        detailText += ` Die modellierte Entfernung sinkt von ${formatNumber(first, "km")} auf ${formatNumber(last, "km")}.`;
      } else {
        summaryText = `Fläche entfernt sich um ${formatNumber(delta, "km")}`;
        detailText += ` Die modellierte Entfernung steigt von ${formatNumber(first, "km")} auf ${formatNumber(last, "km")}.`;
      }
    }
    if (finite(earliestRain)) {
      detailText += earliestRain === 0
        ? " Niederschlag wird bereits am Standort erkannt."
        : ` Erster nasser Standortframe bei +${earliestRain} Minuten.`;
    } else if (radar.coverage0To120) {
      detailText += " Kein Frame zeigt Niederschlag am Standort.";
    }

    summary.textContent = summaryText;
    const detail = ensureDetail(track);
    detail.textContent = detailText;
    track.setAttribute("role", "img");
    track.setAttribute("aria-label", `${summaryText}. ${detailText}`);
  }

  function init() {
    const root = document.getElementById("live-dashboard-root");
    if (!root) {
      window.setTimeout(init, 100);
      return;
    }
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
