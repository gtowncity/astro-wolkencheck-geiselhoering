import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from nowcast_service.config import AppConfig
from nowcast_service.decision_engine import Evidence, RiskState, SourceState
from nowcast_service.sources.cap_runtime import CapSourceRunner
from nowcast_service.sources.radar_runtime import RadarSourceRunner
from nowcast_service.sources.runtime_base import SourceRunError


class DummyClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def __aenter__(self) -> "DummyClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        del args


def module(name: str, **values: object) -> types.ModuleType:
    result = types.ModuleType(name)
    for key, value in values.items():
        setattr(result, key, value)
    return result


def install_radar_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reference: datetime,
    incomplete: bool = False,
    directory_error: bool = False,
    no_download: bool = False,
) -> None:
    candidate = SimpleNamespace(
        name="composite_rv_LATEST.tar", url="https://example", reference_time=None
    )

    class DirectoryClient:
        def __init__(self, client: object) -> None:
            del client

        async def candidates(self, spec: object) -> tuple[object, ...]:
            del spec
            if directory_error:
                raise RuntimeError("directory")
            return (candidate,)

    async def download_atomic(*args: object, **kwargs: object) -> object | None:
        del args, kwargs
        if no_download:
            return None
        return SimpleNamespace(path=Path("archive.tar"), sha256="a" * 64)

    def extract_tar_safely(archive: Path, destination: Path) -> tuple[Path, ...]:
        del archive, destination
        return (Path("frame0"), Path("frame5"))

    def load_radar_frame(path: Path, *, expected_product: str) -> object:
        del path, expected_product
        return SimpleNamespace(metadata=SimpleNamespace(reference_time=reference))

    arrival = SimpleNamespace(
        earliest_minutes=35, estimate_minutes=40, latest_minutes=45, confidence="MEDIUM"
    )
    current = SimpleNamespace(
        lead_minutes=0,
        nearest_component=SimpleNamespace(
            nearest_distance_km=12.5, nearest_bearing_deg=91.0, area_km2=8.0
        ),
        site_maximum_mm_5min=0.0,
    )
    analysis = SimpleNamespace(
        missing_leads=(5,) if incomplete else (),
        coverage_complete_to_60=not incomplete,
        coverage_complete_to_120=not incomplete,
        available_leads=(0, 5, 120),
        frames=(current,),
        arrival=arrival,
        rain_now=False,
        moving_toward_site=True,
        maximum_site_amount_mm_5min=0.2,
        maximum_site_amount_lead_minutes=40,
    )

    def analyze_radar_cycle(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return analysis

    def radar_hazard_evidence(*args: object, **kwargs: object) -> tuple[Evidence, ...]:
        del args, kwargs
        return (Evidence("DWD_RV", RiskState.RED, "RADAR_ARRIVAL_WITHIN_60_MIN", "Arrival"),)

    monkeypatch.setitem(
        sys.modules,
        "nowcast_service.downloads.remote",
        module(
            "nowcast_service.downloads.remote",
            DownloadLimits=lambda **kwargs: kwargs,
            download_atomic=download_atomic,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "nowcast_service.downloads.safe_tar",
        module("nowcast_service.downloads.safe_tar", extract_tar_safely=extract_tar_safely),
    )
    monkeypatch.setitem(
        sys.modules,
        "nowcast_service.sources.dwd_directory",
        module(
            "nowcast_service.sources.dwd_directory",
            DwdDirectoryClient=DirectoryClient,
            RV_SPEC=object(),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "nowcast_service.sources.radar_hdf5",
        module("nowcast_service.sources.radar_hdf5", load_radar_frame=load_radar_frame),
    )
    monkeypatch.setitem(
        sys.modules,
        "nowcast_service.sources.radar_analysis",
        module(
            "nowcast_service.sources.radar_analysis",
            analyze_radar_cycle=analyze_radar_cycle,
            radar_hazard_evidence=radar_hazard_evidence,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("age_minutes", "expected"),
    [(5, SourceState.LIVE), (16, SourceState.STALE), (31, SourceState.FAILED)],
)
async def test_radar_runner_maps_freshness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, age_minutes: int, expected: SourceState
) -> None:
    evaluated = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    install_radar_modules(monkeypatch, reference=evaluated - timedelta(minutes=age_minutes))
    monkeypatch.setattr("nowcast_service.sources.radar_runtime.httpx.AsyncClient", DummyClient)
    snapshot = await RadarSourceRunner(config=AppConfig(), data_dir=tmp_path).run(
        evaluated_at=evaluated
    )
    assert snapshot.state is expected
    assert snapshot.payload["arrivalMinutes"] == 40
    assert snapshot.payload["nearestPrecipitationDirection"] == "E"
    assert snapshot.evidence[0].state is RiskState.RED


@pytest.mark.asyncio
async def test_radar_runner_rejects_incomplete_and_discovery_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluated = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    install_radar_modules(monkeypatch, reference=evaluated, incomplete=True)
    monkeypatch.setattr("nowcast_service.sources.radar_runtime.httpx.AsyncClient", DummyClient)
    with pytest.raises(SourceRunError):
        await RadarSourceRunner(config=AppConfig(), data_dir=tmp_path).run(evaluated_at=evaluated)
    install_radar_modules(monkeypatch, reference=evaluated, directory_error=True)
    with pytest.raises(SourceRunError) as directory:
        await RadarSourceRunner(config=AppConfig(), data_dir=tmp_path).run(evaluated_at=evaluated)
    assert directory.value.code == "RADAR_DIRECTORY_FAILED"
    install_radar_modules(monkeypatch, reference=evaluated, no_download=True)
    with pytest.raises(SourceRunError) as cache:
        await RadarSourceRunner(config=AppConfig(), data_dir=tmp_path).run(evaluated_at=evaluated)
    assert cache.value.code == "RADAR_CYCLE_FAILED"


class CapInfo:
    def __init__(
        self, *, event: str = "GEWITTER", in_force: bool = True, expires: datetime
    ) -> None:
        self.event, self.severity, self.urgency, self.certainty = (
            event,
            "Minor",
            "Immediate",
            "Likely",
        )
        self.effective = self.onset = expires - timedelta(hours=1)
        self.expires, self.headline, self.description, self.instruction = (
            expires,
            event,
            "Official text",
            "Protect equipment",
        )
        self._in_force = in_force

    def is_in_force(self, now: datetime) -> bool:
        del now
        return self._in_force


class CapAlert:
    def __init__(self, identifier: str, info: CapInfo) -> None:
        self.identifier, self._info = identifier, info

    def german_info(self) -> CapInfo:
        return self._info


def install_cap_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    evaluated: datetime,
    matched: tuple[CapAlert, ...] = (),
    unresolved: tuple[CapAlert, ...] = (),
    warning_area_error: bool = False,
    directory_error: bool = False,
    no_download: bool = False,
) -> None:
    del evaluated
    candidate = SimpleNamespace(name="cap.zip", url="https://example", reference_time=None)

    class WarningClient:
        def __init__(self, client: object) -> None:
            del client

        async def fetch(self, **kwargs: object) -> object:
            del kwargs
            if warning_area_error:
                raise RuntimeError("wfs")
            return object()

    class DirectoryClient:
        def __init__(self, client: object) -> None:
            del client

        async def candidates(self, spec: object) -> tuple[object, ...]:
            del spec
            if directory_error:
                raise RuntimeError("directory")
            return (candidate,)

    async def download_atomic(*args: object, **kwargs: object) -> object | None:
        del args, kwargs
        if no_download:
            return None
        return SimpleNamespace(path=Path("cap.zip"), sha256="c" * 64)

    result = SimpleNamespace(
        location=SimpleNamespace(matched=matched, unresolved=unresolved),
        archive_entries=3,
        parsed_alerts=3,
    )

    def load_cap_archive(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return result

    monkeypatch.setitem(
        sys.modules,
        "nowcast_service.downloads.remote",
        module(
            "nowcast_service.downloads.remote",
            DownloadLimits=lambda **kwargs: kwargs,
            download_atomic=download_atomic,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "nowcast_service.sources.cap_snapshot",
        module("nowcast_service.sources.cap_snapshot", load_cap_archive=load_cap_archive),
    )
    monkeypatch.setitem(
        sys.modules,
        "nowcast_service.sources.dwd_cap_directory",
        module(
            "nowcast_service.sources.dwd_cap_directory",
            DwdCapDirectoryClient=DirectoryClient,
            CAP_COMMUNE_SPEC=object(),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "nowcast_service.sources.warning_area_index",
        module("nowcast_service.sources.warning_area_index", DwdWarningAreaClient=WarningClient),
    )


@pytest.mark.asyncio
async def test_cap_runner_filters_future_and_maps_active_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    active = CapAlert("active", CapInfo(expires=now + timedelta(hours=1)))
    future = CapAlert("future", CapInfo(in_force=False, expires=now + timedelta(hours=2)))
    install_cap_modules(monkeypatch, evaluated=now, matched=(active, future))
    monkeypatch.setattr("nowcast_service.sources.cap_runtime.httpx.AsyncClient", DummyClient)
    snapshot = await CapSourceRunner(config=AppConfig(), data_dir=tmp_path).run(evaluated_at=now)
    assert snapshot.state is SourceState.LIVE
    assert snapshot.payload["activeCount"] == 1
    assert snapshot.evidence[0].state is RiskState.RED


@pytest.mark.asyncio
async def test_cap_runner_rejects_unresolved_and_discovery_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    unresolved = CapAlert("unknown", CapInfo(expires=now + timedelta(hours=1)))
    install_cap_modules(monkeypatch, evaluated=now, unresolved=(unresolved,))
    monkeypatch.setattr("nowcast_service.sources.cap_runtime.httpx.AsyncClient", DummyClient)
    with pytest.raises(SourceRunError):
        await CapSourceRunner(config=AppConfig(), data_dir=tmp_path).run(evaluated_at=now)
    for warning, directory, expected in (
        (True, False, "CAP_WARNING_AREA_FAILED"),
        (False, True, "CAP_DIRECTORY_FAILED"),
    ):
        install_cap_modules(
            monkeypatch, evaluated=now, warning_area_error=warning, directory_error=directory
        )
        with pytest.raises(SourceRunError) as error:
            await CapSourceRunner(config=AppConfig(), data_dir=tmp_path).run(evaluated_at=now)
        assert error.value.code == expected
