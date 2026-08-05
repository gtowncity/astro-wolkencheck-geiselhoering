from datetime import UTC, datetime

import httpx
import pytest

from nowcast_service.sources.dwd_directory import (
    DwdDirectoryClient,
    DwdDirectoryError,
    RV_SPEC,
    WN_SPEC,
    parse_directory_entries,
    ranked_candidates,
)


RV_HTML = """
<html><body>
<a href="../">parent</a>
<a href="composite_rv_20260804_2315.tar">old</a>
<a href="composite_rv_20260804_2320.tar">new</a>
<a href="composite_rv_LATEST.tar">latest</a>
<a href="https://evil.example/file.tar">evil</a>
<a href="/outside.tar">outside</a>
</body></html>
"""


def test_directory_parser_only_accepts_same_directory_files() -> None:
    entries = parse_directory_entries(RV_SPEC.directory_url, RV_HTML)

    assert [entry.name for entry in entries] == [
        "composite_rv_20260804_2315.tar",
        "composite_rv_20260804_2320.tar",
        "composite_rv_LATEST.tar",
    ]


def test_candidates_put_alias_first_then_newest_canonical_file() -> None:
    entries = parse_directory_entries(RV_SPEC.directory_url, RV_HTML)

    candidates = ranked_candidates(RV_SPEC, entries)

    assert candidates[0].name == "composite_rv_LATEST.tar"
    assert candidates[0].is_alias is True
    assert candidates[1].name == "composite_rv_20260804_2320.tar"
    assert candidates[1].reference_time == datetime(2026, 8, 4, 23, 20, tzinfo=UTC)


def test_wn_supports_observed_double_underscore_alias() -> None:
    html = '<a href="composite_wn__LATEST.tar">latest</a>'
    entries = parse_directory_entries(WN_SPEC.directory_url, html)

    candidates = ranked_candidates(WN_SPEC, entries)

    assert [candidate.name for candidate in candidates[:2]] == [
        "composite_wn_LATEST.tar",
        "composite_wn__LATEST.tar",
    ]


@pytest.mark.asyncio
async def test_directory_client_rejects_non_html_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/octet-stream"}, content=b"x"
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DwdDirectoryError, match="HTML"):
            await DwdDirectoryClient(client).list(RV_SPEC)
