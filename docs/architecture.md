# v4.3 Live-Core architecture

The local FastAPI process owns all safety decisions. `RuntimeScheduler` runs one locked job per source. RV and CAP runners publish complete `SourceSnapshot` objects only after download, archive and parser validation. `RuntimeCoordinator` updates persistent latches, creates one immutable `DecisionSnapshot`, stores it in SQLite, evaluates alarm transitions and publishes the same snapshot through REST and SSE.

The public GitHub Pages artifact is allowlisted and contains `runtime-config.json` with `mode=PUBLIC` and `localApiAvailable=false`. It never probes localhost and never displays live GREEN.

The existing forecast `index.html` is retained. Local mode injects the live panel without changing forecast calculations.
