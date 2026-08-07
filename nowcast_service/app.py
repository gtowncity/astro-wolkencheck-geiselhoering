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
from nowcast_service.runtime.change_summary import (
    ChangeSummaryError,
    recent_change_summary,
)
from nowcast_service.runtime.coordinator import RuntimeCoordinator
from nowcast_service.satellite_image import SatelliteImageError, SatelliteImageService
from nowcast_service.satellite_latest import render_latest_satellite_image
from nowcast_service.security import CSRF_COOKIE, new_csrf_token, require_csrf
from nowcast_service.sources.runtime_base import SourceRunner

SCHEMA_VERSION = "1.1"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = Path(__file__).resolve().parent
STYLE_ASSETS = (
    "local-live.css",
    "local-live-timeline.css",
    "local-live-details.css",
    "local-live-navigation.css",
    "local-ui-recovery.css",
    "local-satellite-viewer.css",
)
SCRIPT_ASSETS = (
    "local-live.js",
    "local-live-timeline.js",
    "local-live-details.js",
    "local-live-changes.js",
    "local-live-navigation.js",
    "local-ui-recovery.js",
    "local-satellite-viewer.js",
)
LOCAL_ASSETS = {
    **{name: "text/css" for name in STYLE_ASSETS},
    **{name: "text/javascript" for name in SCRIPT_ASSETS},
}

_FORECAST_AUTO_START_BLOCK = """        const period = getPeriod();
        const cache = await loadMatchingCache(period);
        if (cache) render(cache, "cache");
        await refreshData();"""
_FORECAST_MANUAL_START_BLOCK = """        state.dataMode = "idle";
        $("dataModeText").textContent =
          "Zeitraum prüfen und Forecast laden.";"""
_FORECAST_REQUEST_CONFIG = (
    "request: Object.freeze({ timeoutMs: 16000, retries: 1, concurrency: 4 }),"
)
_LOCAL_FORECAST_REQUEST_CONFIG = (
    "request: Object.freeze({ timeoutMs: 10000, retries: 0, concurrency: 8 }),"
)


class SessionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    equipment_state: EquipmentState


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid satellite time") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _prepare_local_index(content: str) -> str:
    """Disable unsolicited forecast traffic and apply bounded local requests."""

    if _FORECAST_AUTO_START_BLOCK not in content:
        raise RuntimeError("Forecast auto-start block is missing from index.html")
    if _FORECAST_REQUEST_CONFIG not in content:
        raise RuntimeError("Forecast request configuration is missing from index.html")

    prepared = content.replace(
        _FORECAST_AUTO_START_BLOCK,
        _FORECAST_MANUAL_START_BLOCK,
        1,
    )
    prepared = prepared.replace(
        _FORECAST_REQUEST_CONFIG,
        _LOCAL_FORECAST_REQUEST_CONFIG,
        1,
    )
    prepared = prepared.replace("Wetter neu laden", "Forecast laden")
    prepared = prepared.replace("Wetter wird geladen ...", "Forecast wird geladen …")
    prepared = prepared.replace(
        "Bitte „Daten aktualisieren“ wählen.",
        "Bitte „Forecast laden“ wählen.",
    )
    return prepared


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
        config=config,
        data_dir=config_store.data_dir,
        source_runners=runners,
    )
    satellite_service = SatelliteImageService(config_store.data_dir)

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
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    application.state.config_store = config_store
    application.state.coordinator = coordinator
    application.state.runtime = coordinator
    application.state.satellite_service = satellite_service

    @application.middleware("http")
    async def add_security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "geolocation=(self), microphone=(), camera=(), fullscreen=(self)"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' https: data: blob:; "
            "script-src 'self' 'unsafe-inline' https:; "
            "style-src 'self' 'unsafe-inline' https:; "
            "connect-src 'self' https:; "
            "img-src 'self' https: data: blob:; "
            "font-src 'self' https: data:"
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
            key=CSRF_COOKIE,
            value=token,
            httponly=False,
            secure=False,
            samesite="strict",
            path="/",
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
            patch.equipment_state,
            configuration_version=updated.configuration_version,
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

    @application.post(
        "/api/v1/alerts/acknowledge",
        dependencies=[Depends(require_csrf)],
    )
    async def acknowledge_alert() -> dict[str, object]:
        return await coordinator.acknowledge()

    @application.post(
        "/api/v1/runtime/refresh",
        dependencies=[Depends(require_csrf)],
    )
    async def refresh_runtime(source: str | None = None) -> dict[str, object]:
        try:
            selected = await coordinator.refresh(source)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown source") from exc
        return {
            "status": "ACCEPTED",
            "sources": selected,
            "generatedAt": utc_now(),
        }

    @application.get("/api/v1/diagnostics")
    def diagnostics() -> dict[str, object]:
        return coordinator.diagnostics_dict()

    @application.get("/api/v1/changes")
    def changes() -> dict[str, object]:
        database_path = config_store.data_dir / "database" / "runtime.sqlite3"
        try:
            return recent_change_summary(database_path)
        except ChangeSummaryError as exc:
            raise HTTPException(
                status_code=503,
                detail="Decision history unavailable",
            ) from exc

    @application.get("/api/v1/satellite/meta")
    async def satellite_meta(product: str = "geocolour") -> dict[str, object]:
        try:
            payload = await satellite_service.metadata(product)
        except SatelliteImageError as exc:
            raise HTTPException(
                status_code=503,
                detail="EUMETSAT satellite metadata unavailable",
            ) from exc
        payload["location"] = {
            "name": config.location.name,
            "latitude": config.location.latitude,
            "longitude": config.location.longitude,
        }
        return payload

    @application.get("/api/v1/satellite/image")
    async def satellite_image(
        product: str = "geocolour",
        time: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        location_name: str | None = None,
    ) -> Response:
        selected_latitude = (
            latitude if latitude is not None else config.location.latitude
        )
        selected_longitude = (
            longitude if longitude is not None else config.location.longitude
        )
        selected_name = location_name or config.location.name
        try:
            if time is None:
                latest = await render_latest_satellite_image(
                    service=satellite_service,
                    product_key=product,
                    latitude=selected_latitude,
                    longitude=selected_longitude,
                    location_name=selected_name,
                )
                headers = {
                    "X-Satellite-Provider": "EUMETSAT",
                    "X-Satellite-Product": latest.product.key,
                    "X-Satellite-Latest": "true",
                    "X-Satellite-Retrieved-At": latest.retrieved_at.isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "X-Satellite-Observation-Time-Source": latest.observation_time_source,
                    "X-Satellite-Cache": "BYPASS",
                }
                if latest.observed_at is not None:
                    age_minutes = max(
                        0.0,
                        (latest.retrieved_at - latest.observed_at).total_seconds() / 60,
                    )
                    headers.update(
                        {
                            "X-Satellite-Observation-Time": latest.observed_at.isoformat().replace(
                                "+00:00", "Z"
                            ),
                            "X-Satellite-Age-Minutes": f"{age_minutes:.1f}",
                            "X-Satellite-Fresh": "true"
                            if age_minutes <= 20
                            else "false",
                        }
                    )
                else:
                    headers["X-Satellite-Fresh"] = "unknown"
                return Response(
                    content=latest.png,
                    media_type="image/png",
                    headers=headers,
                )

            observed_at = _parse_utc(time)
            result = await satellite_service.render(
                product_key=product,
                observed_at=observed_at,
                latitude=selected_latitude,
                longitude=selected_longitude,
                location_name=selected_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SatelliteImageError as exc:
            raise HTTPException(
                status_code=503,
                detail="EUMETSAT satellite image unavailable",
            ) from exc

        age_minutes = max(
            0.0,
            (datetime.now(UTC) - result.observed_at).total_seconds() / 60,
        )
        return Response(
            content=result.png,
            media_type="image/png",
            headers={
                "X-Satellite-Provider": "EUMETSAT",
                "X-Satellite-Product": result.product.key,
                "X-Satellite-Latest": "false",
                "X-Satellite-Observation-Time": result.observed_at.isoformat().replace(
                    "+00:00", "Z"
                ),
                "X-Satellite-Age-Minutes": f"{age_minutes:.1f}",
                "X-Satellite-Fresh": "true" if age_minutes <= 20 else "false",
                "X-Satellite-Cache": "HIT" if result.cached else "MISS",
            },
        )

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
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    @application.get("/{asset_name}")
    def local_asset(asset_name: str) -> FileResponse:
        media_type = LOCAL_ASSETS.get(asset_name)
        if media_type is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(PACKAGE_ROOT / "static" / asset_name, media_type=media_type)

    @application.get("/")
    def root() -> HTMLResponse:
        content = _prepare_local_index(
            (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")
        )
        styles = "".join(
            f'<link rel="stylesheet" href="/{asset}">' for asset in STYLE_ASSETS
        )
        scripts = "".join(
            f'<script src="/{asset}" defer></script>' for asset in SCRIPT_ASSETS
        )
        if "</head>" in content:
            content = content.replace("</head>", styles + "</head>", 1)
        if "</body>" in content:
            content = content.replace("</body>", scripts + "</body>", 1)
        else:
            content += scripts
        return HTMLResponse(content)

    return application


app = create_app(enable_background=True)
