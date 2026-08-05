# Astro-Wolkencheck v4.3 UI redesign architecture

## Scope and invariants

This redesign unifies the local safety runtime and the forecast planner into one product. It starts from commit `ab019f6a16542fb4e2e45b3de35776071635ea79` on the isolated branch `feature/ui-live-dashboard-v4.3`.

The safety core remains authoritative. The UI must never promote `UNKNOWN` to `GREEN`, invent probabilities, hide an active warning, or imply that a green live state guarantees good astrophotography conditions.

The primary user model has three independent axes:

1. **Live safety** — immediate and short-term hardware risk from validated local runtime data.
2. **Capture quality** — forecast-based suitability of the selected night.
3. **Data confidence** — freshness, completeness and technical availability of the inputs.

## Current architecture

- `index.html` contains the full forecast application, its design tokens, navigation, charts and forecast logic.
- `nowcast_service/app.py` serves `index.html` and injects `local-live.css` and `local-live.js` into it.
- `local-live.js` currently creates an independent live panel and inserts it before the first body child. This produces two visually and semantically separate applications.
- `local-live.css` owns a separate status-coloured layout rather than sharing the forecast design system.
- The runtime API already exposes safety snapshots, source snapshots, radar, warnings, alerts, session state, diagnostics and SSE events.

## Data available without backend changes

### Safety snapshot

- risk state, data quality and action
- human reasons and technical reason codes
- equipment state
- evaluation time
- earliest possible clear time
- active hazards
- source snapshots
- algorithm and configuration versions

### Source snapshots

- source and product IDs
- cycle, download, parse, validity and evaluation timestamps
- effective age and state
- completeness
- stale and invalid thresholds
- bounded failure information
- content hash and source input ID for technical details

### Radar payload

- cycle time and frame count
- available and missing lead minutes
- 0–60 and 0–120 minute coverage
- rain at the site now
- movement toward the site
- arrival estimate and arrival window
- nearest precipitation distance and direction
- affected area
- current site intensity in `siteIntensityMm5Min`
- peak site intensity in `peakIntensityMm5Min`
- peak lead time
- unit and hazard hold time

The old UI adapter incorrectly reads `siteIntensity`, `peakIntensity` and `intensityUnit`; the redesign must use the actual payload names.

### CAP payload

- active warnings and count
- event, headline and identifier
- severity, urgency and certainty
- effective, onset and expiry timestamps
- description and instruction
- expiry policy
- archive and parser diagnostics

### Hazards and alarms

- source, risk state, reason and reason code
- observed, latched, last-confirmed and hold times
- clear-cycle progress and clear condition
- acknowledgement state
- current alert and attention requirement

## Backend extensions required

### Radar frame timeline

Expose every validated 5-minute frame from 0 to 120 minutes:

- lead minute
- site rain state and intensity
- coverage sufficiency and valid fraction
- wet site pixel count
- nearest component distance, bearing, area and intensity
- ring statistics for 5, 10, 25 and 50 km

### Runtime job metadata

Expose, where practical:

- last run start and completion
- duration
- next scheduled run
- retry count
- consecutive failures

### Snapshot comparison

Persist or derive a conservative comparison against the previous trusted snapshot:

- previous snapshot ID
- risk-state change
- warnings added and removed
- radar-distance delta
- arrival change
- source recovery or degradation
- concise change summary

## Target information architecture

### App header

One shared header contains product name, location, local time, API state, live age, forecast age, selected period and a coordinated refresh action.

### Primary decision row

Three parallel modules:

- Live safety
- Capture quality
- Data confidence

### Immediate action

A plain-language recommendation combines live state, forecast result, data quality, equipment state, hazards, warnings and radar evidence without inventing facts.

### `JETZT`

- primary decision row
- action recommendation
- equipment state control
- radar summary, site-centred visual and 120-minute timeline
- official warnings with hardware relevance
- active hazards and alarm state
- changes since the previous trusted snapshot
- compact forecast summary

### `NACHT PLANEN`

- best night and best continuous window
- direct night comparison
- explanatory trends and charts

### `STUNDEN`

- compact hourly timeline
- selected-hour explanation and evidence

### `DATEN & DIAGNOSE`

- required and optional sources
- freshness and validity details
- model and ensemble diagnostics
- runtime history
- internal IDs, hashes, codes and full bounded errors

## Visual system

Use one neutral dark design system. Risk colours are semantic accents, never full-page backgrounds. Technical identifiers are progressively disclosed. Empty cards are removed when a compact positive status is sufficient.

The responsive grid targets:

- desktop: 12 columns
- tablet: 2-column composition
- mobile: 1 column with decision and action first

No state may rely on colour alone. All controls require visible focus, keyboard support and understandable accessible names.

## Implementation sequence

1. Architecture and data inventory — this document.
2. Shared tokens, mount point and neutral shell.
3. Three-axis decision row and action recommendation.
4. Correct radar adapter, summary and visualisation.
5. Warning relevance and progressive CAP details.
6. Equipment state, hazards and alarm controls.
7. Forecast navigation and unified overview.
8. Radar timeline, runtime metadata and snapshot comparison.
9. Responsive, accessibility and state coverage.
10. Linux, Windows, coverage, browser and live-smoke validation.

Each phase must be a small logical commit and must preserve the existing API surface unless an additive schema change is required.
