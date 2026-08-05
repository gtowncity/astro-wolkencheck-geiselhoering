# Local API

Read endpoints: `/api/v1/meta`, `/health/live`, `/health/ready`, `/runtime`, `/safety`, `/nowcast`, `/radar`, `/warnings`, `/sources`, `/alerts`, `/diagnostics`, `/config/public`, `/events`.

Writes: `PATCH /session`, `POST /alerts/acknowledge`, `POST /runtime/refresh`. Writes require same-origin CSRF protection. Safety endpoints use `Cache-Control: no-store`. SSE emits snapshot, source-state, alert, acknowledgement and runtime-status events with IDs and supports `Last-Event-ID` replay from a bounded buffer.
