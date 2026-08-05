from pathlib import Path

import httpx
import pytest

from nowcast_service.downloads.remote import (
    DownloadError,
    DownloadLimits,
    download_atomic,
    validate_remote_url,
)

LIMITS = DownloadLimits(max_bytes=16, allowed_suffixes=(".tar",))


def test_remote_url_is_strictly_allowlisted() -> None:
    validate_remote_url("https://opendata.dwd.de/weather/radar/example.tar")

    with pytest.raises(DownloadError):
        validate_remote_url("https://evil.example/weather/radar/example.tar")
    with pytest.raises(DownloadError):
        validate_remote_url("http://opendata.dwd.de/weather/radar/example.tar")
    with pytest.raises(DownloadError):
        validate_remote_url("https://user@opendata.dwd.de/weather/radar/example.tar")


@pytest.mark.asyncio
async def test_download_is_atomic_and_hashed(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": "4", "etag": '"abc"'},
            content=b"test",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await download_atomic(
            client,
            url="https://opendata.dwd.de/weather/radar/example.tar",
            destination_dir=tmp_path,
            limits=LIMITS,
        )

    assert result is not None
    assert result.path.read_bytes() == b"test"
    assert result.bytes_written == 4
    assert result.sha256 == "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    assert result.etag == '"abc"'
    assert not (tmp_path / ".example.tar.part").exists()


@pytest.mark.asyncio
async def test_oversized_download_is_removed(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 17)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DownloadError, match="safety limit"):
            await download_atomic(
                client,
                url="https://opendata.dwd.de/weather/radar/example.tar",
                destination_dir=tmp_path,
                limits=LIMITS,
            )

    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_http_304_returns_none_without_touching_file(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"old"'
        return httpx.Response(304)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await download_atomic(
            client,
            url="https://opendata.dwd.de/weather/radar/example.tar",
            destination_dir=tmp_path,
            limits=LIMITS,
            if_none_match='"old"',
        )

    assert result is None
