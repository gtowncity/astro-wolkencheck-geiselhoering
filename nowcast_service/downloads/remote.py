"""Bounded, allowlisted and atomic HTTP downloads."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

DWD_HOST_ALLOWLIST = frozenset({"opendata.dwd.de"})


class DownloadError(RuntimeError):
    """Raised when a remote file cannot be downloaded safely."""


@dataclass(frozen=True, slots=True)
class DownloadLimits:
    max_bytes: int
    allowed_suffixes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if not self.allowed_suffixes:
            raise ValueError("At least one allowed suffix is required")


@dataclass(frozen=True, slots=True)
class DownloadResult:
    path: Path
    bytes_written: int
    sha256: str
    etag: str | None
    last_modified: str | None


def validate_remote_url(url: str) -> None:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise DownloadError("Remote URL contains an invalid port") from exc
    if parsed.scheme != "https" or parsed.hostname not in DWD_HOST_ALLOWLIST:
        raise DownloadError("Remote URL is not on the DWD HTTPS allowlist")
    if parsed.username or parsed.password or port not in {None, 443}:
        raise DownloadError("Credentials and non-standard ports are forbidden")
    if parsed.query or parsed.fragment:
        raise DownloadError("Query strings and fragments are forbidden for source files")


def _validate_target_name(url: str, limits: DownloadLimits) -> str:
    name = Path(urlparse(url).path).name
    if not name or name in {".", ".."}:
        raise DownloadError("Remote URL has no safe file name")
    if not any(name.endswith(suffix) for suffix in limits.allowed_suffixes):
        raise DownloadError("Remote file type is not allowed")
    return name


async def download_atomic(
    client: httpx.AsyncClient,
    *,
    url: str,
    destination_dir: Path,
    limits: DownloadLimits,
    if_none_match: str | None = None,
    if_modified_since: str | None = None,
) -> DownloadResult | None:
    """Download a file without redirects and publish it by atomic rename.

    Returns ``None`` for HTTP 304.
    """

    validate_remote_url(url)
    name = _validate_target_name(url, limits)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / name
    temporary = destination_dir / f".{name}.part"
    headers = {"Accept": "application/octet-stream", "User-Agent": "AstroWolkencheck/4.3"}
    if if_none_match:
        headers["If-None-Match"] = if_none_match
    if if_modified_since:
        headers["If-Modified-Since"] = if_modified_since

    temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    written = 0
    try:
        async with client.stream("GET", url, headers=headers, follow_redirects=False) as response:
            if response.status_code == 304:
                return None
            if response.status_code != 200:
                raise DownloadError(f"Source download returned HTTP {response.status_code}")
            declared = response.headers.get("content-length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except ValueError as exc:
                    raise DownloadError("Invalid Content-Length") from exc
                if declared_size <= 0 or declared_size > limits.max_bytes:
                    raise DownloadError("Declared download size is outside the safety limit")

            with temporary.open("xb") as handle:
                async for chunk in response.aiter_bytes(64 * 1024):
                    written += len(chunk)
                    if written > limits.max_bytes:
                        raise DownloadError("Download exceeded the safety limit")
                    digest.update(chunk)
                    handle.write(chunk)
                if written == 0:
                    raise DownloadError("Source download was empty")
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(temporary, destination)
            return DownloadResult(
                path=destination,
                bytes_written=written,
                sha256=digest.hexdigest(),
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
