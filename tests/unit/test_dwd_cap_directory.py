from datetime import UTC, datetime

import httpx
import pytest

from nowcast_service.sources.dwd_cap_directory import (
    CAP_CELLS_SPEC,
    CAP_COMMUNE_SPEC,
    DwdCapDirectoryClient,
    ranked_cap_candidates,
)
from nowcast_service.sources.dwd_directory import DirectoryEntry, DwdDirectoryError


def entry(spec_url: str, name: str) -> DirectoryEntry:
    return DirectoryEntry(name=name, url=spec_url + name)


def test_commune_candidates_prefer_german_latest_then_newest_timestamp() -> None:
    entries = (
        entry(CAP_COMMUNE_SPEC.directory_url, CAP_COMMUNE_SPEC.alias),
        entry(
            CAP_COMMUNE_SPEC.directory_url,
            "Z_CAP_C_EDZW_20260804225000_PVW_STATUS_PREMIUMDWD_COMMUNEUNION_DE.zip",
        ),
        entry(
            CAP_COMMUNE_SPEC.directory_url,
            "Z_CAP_C_EDZW_20260804230045_PVW_STATUS_PREMIUMDWD_COMMUNEUNION_DE.zip",
        ),
        entry(
            CAP_COMMUNE_SPEC.directory_url,
            "Z_CAP_C_EDZW_20260804230045_PVW_STATUS_PREMIUMDWD_COMMUNEUNION_EN.zip",
        ),
    )

    candidates = ranked_cap_candidates(CAP_COMMUNE_SPEC, entries)

    assert candidates[0].name == CAP_COMMUNE_SPEC.alias
    assert candidates[0].is_alias is True
    assert candidates[1].reference_time == datetime(2026, 8, 4, 23, 0, 45, tzinfo=UTC)
    assert candidates[1].name.endswith("_DE.zip")
    assert len(candidates) == 3


def test_cells_alias_is_exact_current_dwd_name() -> None:
    assert CAP_CELLS_SPEC.alias == (
        "Z_CAP_C_EDZW_LATEST_PVW_STATUS_PREMIUMCELLS_COMMUNEUNION_DE.zip"
    )


@pytest.mark.asyncio
async def test_cap_directory_client_loads_candidates() -> None:
    html = f'<a href="{CAP_COMMUNE_SPEC.alias}">latest</a>'

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        candidates = await DwdCapDirectoryClient(client).candidates(CAP_COMMUNE_SPEC)

    assert candidates[0].url.endswith(CAP_COMMUNE_SPEC.alias)


@pytest.mark.asyncio
async def test_cap_directory_rejects_http_and_empty_results() -> None:
    responses = iter(
        [
            httpx.Response(503),
            httpx.Response(200, headers={"content-type": "application/json"}, text="{}"),
            httpx.Response(200, headers={"content-type": "text/html"}, text="<html/>"),
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DwdCapDirectoryClient(client)
        with pytest.raises(DwdDirectoryError, match="503"):
            await source.list(CAP_COMMUNE_SPEC)
        with pytest.raises(DwdDirectoryError, match="HTML"):
            await source.list(CAP_COMMUNE_SPEC)
        with pytest.raises(DwdDirectoryError, match="no usable"):
            await source.list(CAP_COMMUNE_SPEC)
