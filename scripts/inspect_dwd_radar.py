"""Live smoke test for current DWD RV/WN archives.

This script is intentionally not part of normal CI. It downloads current data,
validates the archive boundary, and records HDF5 structure without treating the
result as a safety decision.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import h5py
import httpx
import numpy as np

from nowcast_service.downloads.remote import DownloadError, DownloadLimits, download_atomic
from nowcast_service.downloads.safe_tar import UnsafeArchiveError, extract_tar_safely
from nowcast_service.sources.dwd_directory import (
    DwdDirectoryClient,
    DwdProductSpec,
    ProductFile,
    RV_SPEC,
    WN_SPEC,
)


def json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.ndarray):
        return [json_value(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def attrs(obj: h5py.AttributeManager) -> dict[str, Any]:
    return {str(key): json_value(value) for key, value in obj.items()}


def inspect_hdf5(path: Path) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    with h5py.File(path, "r") as handle:
        def visitor(name: str, obj: h5py.Group | h5py.Dataset) -> None:
            item: dict[str, Any] = {
                "path": f"/{name}",
                "kind": "dataset" if isinstance(obj, h5py.Dataset) else "group",
                "attributes": attrs(obj.attrs),
            }
            if isinstance(obj, h5py.Dataset):
                item.update(
                    {
                        "shape": list(obj.shape),
                        "dtype": str(obj.dtype),
                        "chunks": list(obj.chunks) if obj.chunks else None,
                        "compression": obj.compression,
                    }
                )
            nodes.append(item)

        handle.visititems(visitor)
        return {
            "name": path.name,
            "bytes": path.stat().st_size,
            "rootAttributes": attrs(handle.attrs),
            "nodes": nodes,
        }


async def download_first_valid(
    client: httpx.AsyncClient,
    spec: DwdProductSpec,
    work_dir: Path,
) -> tuple[ProductFile, Path, list[dict[str, str]]]:
    candidates = await DwdDirectoryClient(client).candidates(spec)
    attempts: list[dict[str, str]] = []
    for candidate in candidates[:8]:
        try:
            result = await download_atomic(
                client,
                url=candidate.url,
                destination_dir=work_dir / "archives",
                limits=DownloadLimits(
                    max_bytes=128 * 1024 * 1024,
                    allowed_suffixes=(".tar",),
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
    raise RuntimeError(f"No valid downloadable candidate for {spec.product}: {attempts}")


async def inspect_product(
    client: httpx.AsyncClient,
    spec: DwdProductSpec,
    root: Path,
) -> dict[str, Any]:
    candidate, archive, attempts = await download_first_valid(client, spec, root / spec.product)
    extracted_dir = root / spec.product / "extracted"
    try:
        members = extract_tar_safely(archive, extracted_dir)
    except UnsafeArchiveError as exc:
        raise RuntimeError(f"Archive validation failed for {candidate.name}: {exc}") from exc

    sample_paths = list(members[:1])
    if len(members) > 1:
        sample_paths.append(members[-1])
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
        "members": [{"name": item.name, "bytes": item.stat().st_size} for item in members],
        "samples": [inspect_hdf5(item) for item in sample_paths],
    }


async def run(output: Path) -> None:
    timeout = httpx.Timeout(connect=20, read=90, write=30, pool=20)
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=1)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        with tempfile.TemporaryDirectory(prefix="astro-dwd-smoke-") as temporary:
            root = Path(temporary)
            report: dict[str, Any] = {
                "generatedAt": datetime.now(UTC).isoformat(),
                "purpose": "live source structure verification; not a safety decision",
                "products": [],
            }
            for spec in (RV_SPEC, WN_SPEC):
                try:
                    report["products"].append(await inspect_product(client, spec, root))
                except Exception as exc:
                    report["products"].append(
                        {
                            "product": spec.product,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(output.read_text(encoding="utf-8"))
            if any("error" in product for product in report["products"]):
                raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.output))


if __name__ == "__main__":
    main()
