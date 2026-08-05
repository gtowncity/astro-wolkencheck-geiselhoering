import re

import httpx
import pytest

from nowcast_service.sources.dwd_directory import (
    RV_SPEC,
    DwdDirectoryClient,
    DwdDirectoryError,
    DwdProductSpec,
)


def test_product_spec_rejects_non_dwd_and_missing_slash() -> None:
    with pytest.raises(ValueError):
        DwdProductSpec(
            product="BAD",
            directory_url="https://evil.example/path/",
            timestamp_pattern=re.compile(r"(?P<timestamp>.+)"),
            aliases=(),
        )
    with pytest.raises(ValueError, match="end with"):
        DwdProductSpec(
            product="BAD",
            directory_url="https://opendata.dwd.de/path",
            timestamp_pattern=re.compile(r"(?P<timestamp>.+)"),
            aliases=(),
        )


def test_reference_time_parser_ignores_unrelated_names() -> None:
    assert RV_SPEC.parse_reference_time("unrelated.tar") is None


@pytest.mark.asyncio
async def test_directory_client_rejects_http_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DwdDirectoryError, match="503"):
            await DwdDirectoryClient(client).list(RV_SPEC)


@pytest.mark.asyncio
async def test_directory_client_rejects_empty_directory() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DwdDirectoryError, match="no usable"):
            await DwdDirectoryClient(client).list(RV_SPEC)


@pytest.mark.asyncio
async def test_candidates_uses_loaded_directory() -> None:
    html = '<a href="composite_rv_20260805_0630.tar">current</a>'

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        candidates = await DwdDirectoryClient(client).candidates(RV_SPEC)

    assert candidates[0].is_alias is True
    assert candidates[1].name == "composite_rv_20260805_0630.tar"
