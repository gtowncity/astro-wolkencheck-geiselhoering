"""Resolve current DWD CAP status ZIP archives for German-language alerts."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from nowcast_service.sources.dwd_directory import (
    DirectoryEntry,
    DwdDirectoryError,
    parse_directory_entries,
)


@dataclass(frozen=True, slots=True)
class CapProductFile:
    product: str
    name: str
    url: str
    reference_time: datetime | None
    is_alias: bool


@dataclass(frozen=True, slots=True)
class CapProductSpec:
    product: str
    directory_url: str
    canonical_pattern: re.Pattern[str]
    alias: str

    def parse_reference_time(self, name: str) -> datetime | None:
        match = self.canonical_pattern.fullmatch(name)
        if match is None:
            return None
        return datetime.strptime(match.group("timestamp"), "%Y%m%d%H%M%S").replace(
            tzinfo=UTC
        )


CAP_COMMUNE_SPEC = CapProductSpec(
    product="DWD_CAP_COMMUNE",
    directory_url=(
        "https://opendata.dwd.de/weather/alerts/cap/COMMUNEUNION_DWD_STAT/"
    ),
    canonical_pattern=re.compile(
        r"Z_CAP_C_EDZW_(?P<timestamp>\d{14})_PVW_STATUS_"
        r"PREMIUMDWD_COMMUNEUNION_DE\.zip"
    ),
    alias=(
        "Z_CAP_C_EDZW_LATEST_PVW_STATUS_PREMIUMDWD_COMMUNEUNION_DE.zip"
    ),
)

CAP_CELLS_SPEC = CapProductSpec(
    product="DWD_CAP_CELLS",
    directory_url=(
        "https://opendata.dwd.de/weather/alerts/cap/COMMUNEUNION_CELLS_STAT/"
    ),
    canonical_pattern=re.compile(
        r"Z_CAP_C_EDZW_(?P<timestamp>\d{14})_PVW_STATUS_"
        r"PREMIUMCELLS_COMMUNEUNION_DE\.zip"
    ),
    alias=(
        "Z_CAP_C_EDZW_LATEST_PVW_STATUS_PREMIUMCELLS_COMMUNEUNION_DE.zip"
    ),
)


def ranked_cap_candidates(
    spec: CapProductSpec, entries: Iterable[DirectoryEntry]
) -> tuple[CapProductFile, ...]:
    by_name = {entry.name: entry for entry in entries}
    alias_entry = by_name.get(spec.alias)
    alias_url = (
        alias_entry.url
        if alias_entry is not None
        else spec.directory_url + spec.alias
    )
    candidates = [
        CapProductFile(
            product=spec.product,
            name=spec.alias,
            url=alias_url,
            reference_time=None,
            is_alias=True,
        )
    ]
    timestamped: list[CapProductFile] = []
    for entry in by_name.values():
        reference_time = spec.parse_reference_time(entry.name)
        if reference_time is None:
            continue
        timestamped.append(
            CapProductFile(
                product=spec.product,
                name=entry.name,
                url=entry.url,
                reference_time=reference_time,
                is_alias=False,
            )
        )
    timestamped.sort(
        key=lambda item: item.reference_time or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    candidates.extend(timestamped)
    return tuple(candidates)


class DwdCapDirectoryClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def list(self, spec: CapProductSpec) -> tuple[DirectoryEntry, ...]:
        response = await self._client.get(
            spec.directory_url,
            headers={"Accept": "text/html", "User-Agent": "AstroWolkencheck/4.3"},
            follow_redirects=False,
        )
        if response.status_code != 200:
            raise DwdDirectoryError(
                f"DWD CAP directory returned HTTP {response.status_code}"
            )
        if "html" not in response.headers.get("content-type", "").casefold():
            raise DwdDirectoryError("DWD CAP directory did not return HTML")
        if len(response.content) > 16 * 1024 * 1024:
            raise DwdDirectoryError("DWD CAP directory exceeds the safety limit")
        entries = parse_directory_entries(spec.directory_url, response.text)
        if not entries:
            raise DwdDirectoryError("DWD CAP directory contained no usable links")
        return entries

    async def candidates(self, spec: CapProductSpec) -> tuple[CapProductFile, ...]:
        return ranked_cap_candidates(spec, await self.list(spec))
