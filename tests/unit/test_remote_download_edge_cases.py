from pathlib import Path

import httpx
import pytest

from nowcast_service.downloads.remote import (
    DownloadError,
    DownloadLimits,
    download_atomic,
    validate_remote_url,
)


def test_download_limits_require_positive_size_and_suffix() -> None:
    with pytest.raises(ValueError, match="positive"):
        DownloadLimits(max_bytes=0, allowed_suffixes=(".tar",))
    with pytest.raises(ValueError, match="suffix"):
        DownloadLimits(max_bytes=1, allowed_suffixes=())


def test_remote_url_rejects_query_port_and_invalid_port() -> None:
    with pytest.raises(DownloadError, match="Query"):
        validate_remote_url("https://opendata.dwd.de/file.tar?x=1")
    with pytest.raises(DownloadError, match="ports"):
        validate_remote_url("https://opendata.dwd.de:444/file.tar")
    with pytest.raises(DownloadError, match="invalid port"):
        validate_remote_url("https://opendata.dwd.de:bad/file.tar")


@pytest.mark.asyncio
async def test_download_rejects_wrong_suffix_before_request(tmp_path: Path) -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(DownloadError, match="file type"):
            await download_atomic(
                client,
                url="https://opendata.dwd.de/file.exe",
                destination_dir=tmp_path,
                limits=DownloadLimits(max_bytes=10, allowed_suffixes=(".tar",)),
            )


@pytest.mark.asyncio
async def test_download_rejects_http_error_invalid_length_and_empty_body(tmp_path: Path) -> None:
    responses = iter(
        [
            httpx.Response(503),
            httpx.Response(200, headers={"content-length": "bad"}, content=b"x"),
            httpx.Response(200, headers={"content-length": "99"}, content=b"x"),
            httpx.Response(200, content=b""),
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    limits = DownloadLimits(max_bytes=8, allowed_suffixes=(".tar",))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DownloadError, match="503"):
            await download_atomic(
                client,
                url="https://opendata.dwd.de/file.tar",
                destination_dir=tmp_path,
                limits=limits,
            )
        with pytest.raises(DownloadError, match="Content-Length"):
            await download_atomic(
                client,
                url="https://opendata.dwd.de/file.tar",
                destination_dir=tmp_path,
                limits=limits,
            )
        with pytest.raises(DownloadError, match="Declared"):
            await download_atomic(
                client,
                url="https://opendata.dwd.de/file.tar",
                destination_dir=tmp_path,
                limits=limits,
            )
        with pytest.raises(DownloadError, match="empty"):
            await download_atomic(
                client,
                url="https://opendata.dwd.de/file.tar",
                destination_dir=tmp_path,
                limits=limits,
            )

    assert not list(tmp_path.iterdir())
