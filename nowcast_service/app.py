"""Local FastAPI service for the hybrid Astro-Wolkencheck application."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

from nowcast_service import __version__
from nowcast_service.config import AppConfig, ConfigStore
from nowcast_service.decision_engine import EquipmentState, RiskState
from nowcast_service.runtime_state import RuntimeState
from nowcast_service.security import CSRF_COOKIE, new_csrf_token, require_csrf

SCHEMA_VERSION = "1.0"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class SessionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equipment_state: EquipmentState


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def create_app(*, data_dir: Path | None = None) -> FastAPI:
    config_store = ConfigStore(data_dir)
    config = config_store.load()
    runtime = RuntimeState(equipment_state=config.equipment_state)

    application = FastAPI(
        title="Astro-Wolkencheck local service",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    application.state.config_store = config_store
    application.state.runtime = runtime

    @application.middleware("http")
    async def add_security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(self), microphone=(), camera=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.get("/runtime-config.json")
    def runtime_config() -> dict[str, object]:
        return {"mode": "LOCAL", "localApiAvailable": True, "apiBase": "/api/v1"}

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
        decision = runtime.decision()
        return {
            "status": "READY",
            "safetyDataReady": decision.state is not RiskState.UNKNOWN,
            "hardwareRisk": decision.state,
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
        return {
            "schemaVersion": SCHEMA_VERSION,
            "equipmentState": runtime.equipment_state,
            "generatedAt": utc_now(),
        }

    @application.patch("/api/v1/session", dependencies=[Depends(require_csrf)])
    def patch_session(patch: SessionPatch) -> dict[str, object]:
        current: AppConfig = config_store.load()
        updated = current.model_copy(update={"equipment_state": patch.equipment_state})
        config_store.save(updated)
        runtime.set_equipment_state(patch.equipment_state)
        return get_session()

    @application.get("/api/v1/safety")
    def safety() -> dict[str, object]:
        decision = runtime.decision()
        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": utc_now(),
            "equipmentState": runtime.equipment_state,
            "hardwareRisk": {
                "state": decision.state,
                "dataQuality": decision.data_quality,
                "action": decision.action,
                "reasonCodes": decision.reason_codes,
                "reasons": decision.reasons,
            },
            "sources": [
                {
                    "source": item.source,
                    "required": item.required,
                    "supporting": item.supporting,
                    "state": item.state,
                    "detail": item.detail,
                }
                for item in runtime.source_health()
            ],
        }

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

    @application.get("/")
    def root() -> FileResponse:
        return FileResponse(PROJECT_ROOT / "index.html")

    return application


app = create_app()
