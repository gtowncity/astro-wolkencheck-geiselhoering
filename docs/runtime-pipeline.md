# Runtime pipeline

1. Scheduler resolves the newest DWD candidate.
2. Downloader enforces fixed HTTPS hosts, limits, hashes and atomic files.
3. Existing safe archive and strict parser modules validate the full cycle.
4. The source runner emits a complete immutable source snapshot or a sanitized failure.
5. Coordinator updates source-specific latches and evaluates fail-safe risk.
6. Decision, latches, alerts and acknowledgement audit are persisted transactionally.
7. REST, SSE and local UI receive the same snapshot ID.

A failed new cycle retains the last successful payload for diagnostics but cannot satisfy the current GREEN gate.
