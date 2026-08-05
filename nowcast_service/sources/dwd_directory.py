"""Resolve current DWD Open Data product files without trusting one alias."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

DWD_OPEN_DATA_HOSTS = frozenset({"opendata.dwd.de"})


class DwdDirectoryError(RuntimeError):
    """Raised when a DWD directory response cannot be used safely."""


def _validate_dwd_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in DWD_OPEN_DATA_HOSTS:
        raise ValueError("Only HTTPS URLs on the approved DWD Open Data host are allowed")
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("Credentials and non-standard ports are not allowed")


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class ProductFile:
    product: str
    name: str
    url: str
    reference_time: datetime | None
    is_alias: bool


@dataclass(frozen=True, slots=True)
class DwdProductSpec:
    product: str
    directory_url: str
    timestamp_pattern: re.Pattern[str]
    aliases: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_dwd_url(self.directory_url)
        if not self.directory_url.endswith("/"):
            raise ValueError("directory_url must end with /")

    def parse_reference_time(self, name: str) -> datetime | None:
        match = self.timestamp_pattern.fullmatch(name)
        if match is None:
            return None
        stamp = match.group("timestamp")
        return datetime.strptime(stamp, "%Y%m%d_%H%M").replace(tzinfo=UTC)


RV_SPEC = DwdProductSpec(
    product="DWD_RV",
    directory_url="https://opendata.dwd.de/weather/radar/composite/rv/",
    timestamp_pattern=re.compile(r"composite_rv_(?P<timestamp>\d{8}_\d{4})\.tar"),
    aliases=("composite_rv_LATEST.tar",),
)

WN_SPEC = DwdProductSpec(
    product="DWD_WN",
    directory_url="https://opendata.dwd.de/weather/radar/composite/wn/",
    timestamp_pattern=re.compile(r"composite_wn_(?P<timestamp>\d{8}_\d{4})\.tar"),
    aliases=("composite_wn_LATEST.tar", "composite_wn__LATEST.tar"),
)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(href)


def parse_directory_entries(directory_url: str, html: str) -> tuple[DirectoryEntry, ...]:
    """Parse same-directory file links from an Apache-style index."""

    _validate_dwd_url(directory_url)
    parser = _LinkParser()
    parser.feed(html)
    entries: dict[str, DirectoryEntry] = {}
    base = urlparse(directory_url)

    for href in parser.hrefs:
        url = urljoin(directory_url, href)
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != base.hostname:
            continue
        if parsed.query or parsed.fragment or parsed.path.endswith("/"):
            continue
        expected_prefix = base.path
        if not parsed.path.startswith(expected_prefix):
            continue
        name = parsed.path.removeprefix(expected_prefix)
        if not name or "/" in name or name in {".", ".."}:
            continue
        entries[name] = DirectoryEntry(name=name, url=url)

    return tuple(sorted(entries.values(), key=lambda item: item.name))


def ranked_candidates(
    spec: DwdProductSpec, entries: Iterable[DirectoryEntry]
) -> tuple[ProductFile, ...]:
    """Return alias candidates first, then newest timestamped canonical files."""

    by_name = {entry.name: entry for entry in entries}
    candidates: list[ProductFile] = []

    for alias in spec.aliases:
        entry = by_name.get(alias)
        if entry is None:
            candidates.append(
                ProductFile(
                    product=spec.product,
                    name=alias,
                    url=urljoin(spec.directory_url, alias),
                    reference_time=None,
                    is_alias=True,
                )
            )
        else:
            candidates.append(
                ProductFile(
                    product=spec.product,
                    name=entry.name,
                    url=entry.url,
                    reference_time=None,
                    is_alias=True,
                )
            )

    timestamped: list[ProductFile] = []
    for entry in by_name.values():
        reference_time = spec.parse_reference_time(entry.name)
        if reference_time is None:
            continue
        timestamped.append(
            ProductFile(
                product=spec.product,
                name=entry.name,
                url=entry.url,
                reference_time=reference_time,
                is_alias=False,
            )
        )
    timestamped.sort(
        key=lambda item: (item.reference_time or datetime.min.replace(tzinfo=UTC), item.name),
        reverse=True,
    )
    candidates.extend(timestamped)
    return tuple(candidates)


class DwdDirectoryClient:
    """Fetch and parse a DWD product directory with bounded HTTP behavior."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def list(self, spec: DwdProductSpec) -> tuple[DirectoryEntry, ...]:
        response = await self._client.get(
            spec.directory_url,
            headers={"Accept": "text/html", "User-Agent": "AstroWolkencheck/4.3"},
            follow_redirects=False,
        )
        if response.status_code != 200:
            raise DwdDirectoryError(
                f"DWD directory returned HTTP {response.status_code}: {spec.directory_url}"
            )
        content_type = response.headers.get("content-type", "").casefold()
        if "html" not in content_type:
            raise DwdDirectoryError("DWD directory did not return HTML")
        if len(response.content) > 8 * 1024 * 1024:
            raise DwdDirectoryError("DWD directory response exceeds the safety limit")
        entries = parse_directory_entries(spec.directory_url, response.text)
        if not entries:
            raise DwdDirectoryError("DWD directory contained no usable file links")
        return entries

    async def candidates(self, spec: DwdProductSpec) -> tuple[ProductFile, ...]:
        return ranked_candidates(spec, await self.list(spec))
