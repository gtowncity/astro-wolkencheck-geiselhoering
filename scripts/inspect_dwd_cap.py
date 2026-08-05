"""Live structure, semantic and site-resolution smoke test for DWD CAP archives."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from defusedxml import ElementTree

from nowcast_service.downloads.remote import DownloadError, DownloadLimits, download_atomic
from nowcast_service.downloads.safe_zip import UnsafeZipError, validate_zip
from nowcast_service.sources.cap_parser import parse_cap_xml
from nowcast_service.sources.cap_snapshot import load_cap_archive
from nowcast_service.sources.dwd_cap_directory import (
    CAP_CELLS_SPEC,
    CAP_COMMUNE_SPEC,
    CapProductFile,
    CapProductSpec,
    DwdCapDirectoryClient,
)
from nowcast_service.sources.warning_area_index import DwdWarningAreaClient

LONGITUDE = 12.40
LATITUDE = 48.84


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def first_text(root: ElementTree.Element, name: str) -> str | None:
    for element in root.iter():
        if local_name(element.tag) == name and element.text:
            return str(element.text).strip()
    return None


def inspect_xml(content: bytes, name: str) -> dict[str, Any]:
    if len(content) > 2 * 1024 * 1024:
        raise RuntimeError(f"CAP XML exceeds smoke-test limit: {name}")
    root = ElementTree.fromstring(content)
    tags: dict[str, int] = {}
    for element in root.iter():
        key = local_name(element.tag)
        tags[key] = tags.get(key, 0) + 1
    return {
        "name": name,
        "rootTag": local_name(root.tag),
        "namespace": root.tag.partition("}")[0].removeprefix("{") if "}" in root.tag else None,
        "identifier": first_text(root, "identifier"),
        "sender": first_text(root, "sender"),
        "sent": first_text(root, "sent"),
        "status": first_text(root, "status"),
        "msgType": first_text(root, "msgType"),
        "scope": first_text(root, "scope"),
        "language": first_text(root, "language"),
        "event": first_text(root, "event"),
        "severity": first_text(root, "severity"),
        "certainty": first_text(root, "certainty"),
        "urgency": first_text(root, "urgency"),
        "effective": first_text(root, "effective"),
        "onset": first_text(root, "onset"),
        "expires": first_text(root, "expires"),
        "tagCounts": tags,
    }


async def download_first_valid(
    client: httpx.AsyncClient,
    spec: CapProductSpec,
    work_dir: Path,
) -> tuple[CapProductFile, Path, list[dict[str, str]]]:
    candidates = await DwdCapDirectoryClient(client).candidates(spec)
    attempts: list[dict[str, str]] = []
    for candidate in candidates[:6]:
        try:
            result = await download_atomic(
                client,
                url=candidate.url,
                destination_dir=work_dir,
                limits=DownloadLimits(
                    max_bytes=16 * 1024 * 1024,
                    allowed_suffixes=(".zip",),
                ),
            )
            if result is None:
                attempts.append({"name": candidate.name, "result": "not-modified"})
                continue
            attempts.append(
                {
                    "name": candidate.name,
                    "result": "downloaded",
                    "sha256": result.sha256,
                }
            )
            return candidate, result.path, attempts
        except (DownloadError, httpx.HTTPError, OSError) as exc:
            attempts.append(
                {
                    "name": candidate.name,
                    "result": "rejected",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    raise RuntimeError(f"No usable CAP candidate for {spec.product}: {attempts}")


def unresolved_summary(alert: Any, *, now: datetime) -> dict[str, Any] | None:
    info = alert.german_info()
    if info is None or not info.is_in_force(now):
        return None
    geocodes = sorted(
        {
            f"{code.name}={code.value}"
            for area in info.areas
            for code in area.geocodes
        }
    )
    return {
        "identifier": alert.identifier,
        "event": info.event,
        "headline": info.headline,
        "areaDescriptions": [area.description for area in info.areas],
        "geocodes": geocodes,
    }


async def inspect_product(
    client: httpx.AsyncClient,
    spec: CapProductSpec,
    root: Path,
    *,
    now: datetime,
    resolver: Any | None = None,
) -> dict[str, Any]:
    candidate, archive, attempts = await download_first_valid(
        client, spec, root / spec.product
    )
    try:
        infos = validate_zip(archive)
    except UnsafeZipError as exc:
        raise RuntimeError(f"CAP archive validation failed: {exc}") from exc

    samples: list[dict[str, Any]] = []
    semantic_failures: list[dict[str, Any]] = []
    semantic_parsed = 0
    with zipfile.ZipFile(archive, "r") as bundle:
        for index, info in enumerate(infos):
            content = bundle.read(info)
            summary = inspect_xml(content, info.filename)
            if index < 3:
                samples.append(summary)
            try:
                parse_cap_xml(content)
                semantic_parsed += 1
            except Exception as exc:
                if len(semantic_failures) < 20:
                    semantic_failures.append(
                        {
                            "name": info.filename,
                            "error": f"{type(exc).__name__}: {exc}",
                            "summary": summary,
                        }
                    )

    location_resolution: dict[str, Any] | None = None
    if resolver is not None:
        resolved = await asyncio.to_thread(
            load_cap_archive,
            archive,
            now=now,
            longitude=LONGITUDE,
            latitude=LATITUDE,
            resolver=resolver,
        )
        unresolved_in_force = [
            summary
            for alert in resolved.location.unresolved
            if (summary := unresolved_summary(alert, now=now)) is not None
        ]
        location_resolution = {
            "site": {"longitude": LONGITUDE, "latitude": LATITUDE},
            "siteWarningCellIds": list(resolver.ids_covering(LONGITUDE, LATITUDE)),
            "matchedCount": len(resolved.location.matched),
            "nonmatchingCount": len(resolved.location.nonmatching),
            "unresolvedCount": len(resolved.location.unresolved),
            "inForceUnresolvedCount": len(unresolved_in_force),
            "inForceUnresolved": unresolved_in_force[:20],
        }

    return {
        "product": spec.product,
        "candidate": {
            "name": candidate.name,
            "url": candidate.url,
            "isAlias": candidate.is_alias,
            "referenceTime": (
                candidate.reference_time.isoformat() if candidate.reference_time else None
            ),
        },
        "attempts": attempts,
        "archiveBytes": archive.stat().st_size,
        "entries": len(infos),
        "totalExpandedBytes": sum(info.file_size for info in infos),
        "suffixes": sorted({Path(info.filename).suffix.casefold() for info in infos}),
        "maxDepth": max(len(Path(info.filename).parts) for info in infos),
        "semanticParsed": semantic_parsed,
        "semanticFailureCount": len(infos) - semantic_parsed,
        "semanticFailures": semantic_failures,
        "locationResolution": location_resolution,
        "samples": samples,
    }


async def run(output: Path) -> None:
    timeout = httpx.Timeout(connect=20, read=90, write=30, pool=20)
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=1)
    now = datetime.now(UTC)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        with tempfile.TemporaryDirectory(prefix="astro-cap-smoke-") as temporary:
            report: dict[str, Any] = {
                "generatedAt": now.isoformat(),
                "purpose": (
                    "live CAP structure, semantic and Geiselhoering location verification; "
                    "not a safety decision"
                ),
                "warningArea": {},
                "products": [],
            }
            warning_index: Any | None = None
            try:
                warning_index = await DwdWarningAreaClient(client).fetch(
                    longitude=LONGITUDE,
                    latitude=LATITUDE,
                    retrieved_at=now,
                )
                report["warningArea"] = {
                    "sourceSha256": warning_index.source_sha256,
                    "featureCount": len(warning_index.areas),
                    "siteWarningCellIds": list(
                        warning_index.ids_covering(LONGITUDE, LATITUDE)
                    ),
                }
            except Exception as exc:
                report["warningArea"] = {"error": f"{type(exc).__name__}: {exc}"}

            for spec in (CAP_COMMUNE_SPEC, CAP_CELLS_SPEC):
                try:
                    resolver = (
                        warning_index
                        if spec.product == CAP_COMMUNE_SPEC.product
                        else None
                    )
                    report["products"].append(
                        await inspect_product(
                            client,
                            spec,
                            Path(temporary),
                            now=now,
                            resolver=resolver,
                        )
                    )
                except Exception as exc:
                    report["products"].append(
                        {"product": spec.product, "error": f"{type(exc).__name__}: {exc}"}
                    )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(output.read_text(encoding="utf-8"))
            failed = "error" in report["warningArea"] or any(
                "error" in product
                or int(product.get("semanticFailureCount", 0)) > 0
                or int(
                    (product.get("locationResolution") or {}).get(
                        "inForceUnresolvedCount", 0
                    )
                )
                > 0
                for product in report["products"]
            )
            if failed:
                raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.output))


if __name__ == "__main__":
    main()
