"""Local FastAPI service for the hybrid Astro-Wolkencheck application."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

from nowcast_service import __version__
from nowcast_service.config import AppConfig, ConfigStore
from nowcast_service.decision_engine import EquipmentState, RiskState
from nowcast_service.runtime.coordinator import RuntimeCoordinator
from nowcast_service.security import CSRF_COOKIE, new_csrf_token, require_csrf
from nowcast_service.sources.runtime_base import SourceRunner

SCHEMA_VERSION = "1.1"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = Path(__file__).resolve().parent


class SessionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    equipment_state: EquipmentState


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _production_runners(config: AppConfig, data_dir: Path) -> tuple[SourceRunner, ...]:
    from nowcast_service.sources.cap_runtime import CapSourceRunner
    from nowcast_service.sources.radar_runtime import RadarSourceRunner

    return (
        RadarSourceRunner(config=config, data_dir=data_dir),
        CapSourceRunner(config=config, data_dir=data_dir),
    )


def create_app(
    *,
    data_dir: Path | None = None,
    enable_background: bool = False,
    source_runners: tuple[SourceRunner, ...] | None = None,
) -> FastAPI:
    config_store = ConfigStore(data_dir)
    config = config_store.load()
    runners = source_runners
    if runners is None:
        runners = _production_runners(config, config_store.data_dir) if enable_background else ()
    coordinator = RuntimeCoordinator(
        config=config, data_dir=config_store.data_dir, source_runners=runners
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if enable_background:
            await coordinator.start()
        try:
            yield
        finally:
            if enable_background:
                await coordinator.stop()

    application = FastAPI(
        title="Astro-Wolkencheck local service",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    application.state.config_store = config_store
    application.state.coordinator = coordinator
    application.state.runtime = coordinator

    @application.middleware("http")
    async def add_security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(self), microphone=(), camera=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' https: data: blob:; script-src 'self' 'unsafe-inline' https:; "
            "style-src 'self' 'unsafe-inline' https:; connect-src 'self' https:; "
            "img-src 'self' https: data: blob:; font-src 'self' https: data:"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.get("/runtime-config.json")
    def runtime_config() -> dict[str, object]:
        return {
            "mode": "LOCAL",
            "localApiAvailable": True,
            "apiBase": "/api/v1",
            "browserAudioEnabled": config.alerts.browser_audio_enabled,
            "browserNotificationsEnabled": config.alerts.browser_notifications_enabled,
        }

    @application.get("/api/v1/meta")
    def meta() -> dict[str, object]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "appVersion": __version__,
            "mode": "LOCAL",
            "generatedAt": utc_now(),
        }

    @application.get("/api/v1/health/live")
    def health_live() -> dict[str, object]:
        return {"status": "LIVE", "generatedAt": utc_now()}

    @application.get("/api/v1/health/ready")
    def health_ready() -> dict[str, object]:
        snapshot = coordinator.snapshot()
        return {
            "status": "READY",
            "safetyDataReady": snapshot.decision.state is not RiskState.UNKNOWN,
            "hardwareRisk": snapshot.decision.state,
            "snapshotId": snapshot.snapshot_id,
            "generatedAt": utc_now(),
        }

    @application.get("/api/v1/security/csrf")
    def csrf(response: Response) -> dict[str, str]:
        token = new_csrf_token()
        response.set_cookie(
            key=CSRF_COOKIE, value=token, httponly=False, secure=False, samesite="strict", path="/"
        )
        return {"token": token}

    @application.get("/api/v1/session")
    def get_session() -> dict[str, object]:
        snapshot = coordinator.snapshot()
        return {
            "schemaVersion": SCHEMA_VERSION,
            "snapshotId": snapshot.snapshot_id,
            "equipmentState": coordinator.equipment_state,
            "generatedAt": utc_now(),
        }

    @application.patch("/api/v1/session", dependencies=[Depends(require_csrf)])
    async def patch_session(patch: SessionPatch) -> dict[str, object]:
        current = config_store.load()
        updated = current.model_copy(
            update={
                "equipment_state": patch.equipment_state,
                "configuration_version": current.configuration_version + 1,
            }
        )
        config_store.save(updated)
        snapshot = await coordinator.set_equipment_state(
            patch.equipment_state, configuration_version=updated.configuration_version
        )
        return {
            "schemaVersion": SCHEMA_VERSION,
            "snapshotId": snapshot.snapshot_id,
            "equipmentState": coordinator.equipment_state,
            "generatedAt": utc_now(),
        }

    @application.get("/api/v1/safety")
    def safety() -> dict[str, object]:
        return coordinator.snapshot_dict()

    @application.get("/api/v1/runtime")
    def runtime() -> dict[str, object]:
        return coordinator.runtime_dict()

    @application.get("/api/v1/nowcast")
    def nowcast() -> dict[str, object]:
        return coordinator.snapshot_dict()

    @application.get("/api/v1/radar")
    def radar() -> dict[str, object]:
        return coordinator.radar_dict()

    @application.get("/api/v1/warnings")
    def warnings() -> dict[str, object]:
        return coordinator.warnings_dict()

    @application.get("/api/v1/sources")
    def sources() -> list[dict[str, object]]:
        return coordinator.source_dicts()

    @application.get("/api/v1/alerts")
    def alerts() -> dict[str, object]:
        return coordinator.alerts_dict()

    @application.post("/api/v1/alerts/acknowledge", dependencies=[Depends(require_csrf)])
    async def acknowledge_alert() -> dict[str, object]:
        return await coordinator.acknowledge()

    @application.post("/api/v1/runtime/refresh", dependencies=[Depends(require_csrf)])
    async def refresh_runtime(source: str | None = None) -> dict[str, object]:
        try:
            selected = await coordinator.refresh(source)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown source") from exc
        return {"status": "ACCEPTED", "sources": selected, "generatedAt": utc_now()}

    @application.get("/api/v1/diagnostics")
    def diagnostics() -> dict[str, object]:
        return coordinator.diagnostics_dict()

    @application.get("/api/v1/config/public")
    def public_config() -> dict[str, object]:
        current = config_store.load()
        return {
            "schemaVersion": current.schema_version,
            "location": {
                "name": current.location.name,
                "timezone": current.location.timezone,
                "precision": current.location.precision,
            },
            "thresholds": current.thresholds.model_dump(mode="json"),
            "rainSensorEnabled": current.rain_sensor.enabled,
        }

    @application.get("/api/v1/events")
    def events(last_event_id: str | None = Header(default=None)) -> StreamingResponse:
        try:
            cursor = int(last_event_id or "0")
        except ValueError:
            cursor = 0

        async def generate() -> AsyncIterator[str]:
            initial = json.dumps(
                {
                    "eventId": 0,
                    "eventType": "snapshot",
                    "createdAt": utc_now(),
                    "payload": coordinator.snapshot_dict(),
                },
                separators=(",", ":"),
            )
            if cursor == 0:
                yield f"id: 0\nevent: snapshot\ndata: {initial}\n\n"
            async for item in coordinator.events.stream(last_event_id=cursor):
                yield item

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @application.get("/local-live.js")
    def local_live_js() -> FileResponse:
        return FileResponse(PACKAGE_ROOT / "static" / "local-live.js", media_type="text/javascript")

    @application.get("/local-live.css")
    def local_live_css() -> FileResponse:
        return FileResponse(PACKAGE_ROOT / "static" / "local-live.css", media_type="text/css")

    @application.get("/")
    def root() -> HTMLResponse:
        content = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")
        style = '<link rel="stylesheet" href="/local-live.css">'
        script = '<script src="/local-live.js" defer></script>'
        if "</head>" in content:
            content = content.replace("</head>", style + "</head>", 1)
        if "</body>" in content:
            content = content.replace("</body>", script + "</body>", 1)
        else:
            content += script
        return HTMLResponse(content)

    return application


app = create_app(enable_background=True)
