"""Verify current EUMETSAT MTG imagery and render a pinned Bavaria frame."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from nowcast_service.satellite_image import SatelliteImageService

PREFERRED_PRODUCTS = (
    "geocolour",
    "infrared",
    "cloudtype",
    "cloudphase",
    "lightning",
)


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def red_pixel_count(content: bytes) -> int:
    with Image.open(io.BytesIO(content)) as opened:
        image: Image.Image = opened.convert("RGB")
    return sum(
        1
        for red, green, blue in image.getdata()
        if red > 180 and green < 90 and blue < 110
    )


def generated_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def product_records(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RuntimeError("Satellite metadata has no product list")
    records: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            records.append(item)
    return records


def report_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "key": item.get("key"),
            "title": item.get("title"),
            "available": item.get("available"),
            "latestTime": item.get("latestTime"),
            "latestAgeMinutes": item.get("latestAgeMinutes"),
            "fresh": item.get("fresh"),
            "frameCount": item.get("frameCount"),
        }
        for item in products
    ]


def select_fresh_product(
    products: list[dict[str, Any]],
    *,
    preferred: str,
    maximum_age_minutes: float,
) -> dict[str, Any] | None:
    by_key = {
        str(item.get("key")): item
        for item in products
        if item.get("available") is True
    }
    order = (preferred, *[key for key in PREFERRED_PRODUCTS if key != preferred])
    for key in order:
        item = by_key.get(key)
        age = item.get("latestAgeMinutes") if item is not None else None
        if isinstance(age, (int, float)) and float(age) <= maximum_age_minutes:
            return item
    candidates = [
        item
        for item in by_key.values()
        if isinstance(item.get("latestAgeMinutes"), (int, float))
    ]
    return min(
        candidates,
        key=lambda item: float(item["latestAgeMinutes"]),
        default=None,
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="awc-satellite-smoke-") as temporary:
        service = SatelliteImageService(Path(temporary))
        first_metadata = await service.metadata(args.product)
        products = product_records(first_metadata.get("products"))
        available = [item for item in products if item.get("available") is True]
        if len(available) < args.minimum_products:
            raise RuntimeError(
                f"Only {len(available)} EUMETSAT products are available; "
                f"expected at least {args.minimum_products}; "
                f"products={json.dumps(report_products(products), ensure_ascii=False)}"
            )

        selected = select_fresh_product(
            products,
            preferred=args.product,
            maximum_age_minutes=args.maximum_age_minutes,
        )
        if selected is None:
            raise RuntimeError(
                "No EUMETSAT product exposes a real observation time; "
                f"products={json.dumps(report_products(products), ensure_ascii=False)}"
            )
        selected_key = str(selected.get("key"))
        selected_age = selected.get("latestAgeMinutes")
        if not isinstance(selected_age, (int, float)):
            raise RuntimeError(
                f"Selected EUMETSAT product {selected_key} has no numeric age"
            )
        if float(selected_age) > args.maximum_age_minutes:
            raise RuntimeError(
                "No real EUMETSAT product satisfies the freshness limit; "
                f"freshest={selected_key}:{float(selected_age):.1f} min; "
                f"limit={args.maximum_age_minutes:.1f} min; "
                f"products={json.dumps(report_products(products), ensure_ascii=False)}"
            )

        metadata = (
            first_metadata
            if selected_key == args.product
            else await service.metadata(selected_key)
        )
        frames = metadata.get("frames")
        if not isinstance(frames, list) or not frames:
            raise RuntimeError(
                f"Selected satellite product {selected_key} has no real frames"
            )

        latest = parse_time(str(frames[-1]))
        age_minutes = max(
            0.0,
            (datetime.now(UTC) - latest).total_seconds() / 60,
        )
        if age_minutes > args.maximum_age_minutes:
            raise RuntimeError(
                f"Latest real {selected_key} frame is {age_minutes:.1f} minutes old; "
                f"limit is {args.maximum_age_minutes} minutes; "
                f"products={json.dumps(report_products(products), ensure_ascii=False)}"
            )

        result = await service.render(
            product_key=selected_key,
            observed_at=latest,
            latitude=args.latitude,
            longitude=args.longitude,
            location_name=args.location_name,
        )
        with Image.open(io.BytesIO(result.png)) as image:
            image.verify()
        red_pixels = red_pixel_count(result.png)
        if red_pixels < 20:
            raise RuntimeError("Rendered real satellite frame has no visible red site pin")

        args.image_output.parent.mkdir(parents=True, exist_ok=True)
        args.image_output.write_bytes(result.png)
        with Image.open(io.BytesIO(result.png)) as rendered:
            image_size = list(rendered.size)
        report: dict[str, Any] = {
            "status": "PASS",
            "provider": metadata.get("provider"),
            "satellite": metadata.get("satellite"),
            "requestedProduct": args.product,
            "selectedProduct": metadata.get("selectedProduct"),
            "availableProducts": report_products(products),
            "selectedFrameCount": len(frames),
            "latestFrame": latest.isoformat().replace("+00:00", "Z"),
            "latestAgeMinutes": round(age_minutes, 2),
            "maximumAllowedAgeMinutes": args.maximum_age_minutes,
            "imageSize": image_size,
            "redPinPixels": red_pixels,
            "cache": "HIT" if result.cached else "MISS",
            "generatedAt": generated_at(),
        }
        return report


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", default="geocolour")
    parser.add_argument("--latitude", type=float, default=48.84)
    parser.add_argument("--longitude", type=float, default=12.40)
    parser.add_argument("--location-name", default="Geiselhöring")
    parser.add_argument("--maximum-age-minutes", type=float, default=20.0)
    parser.add_argument("--minimum-products", type=int, default=2)
    parser.add_argument(
        "--report-output",
        type=Path,
        default=Path("satellite-smoke-report.json"),
    )
    parser.add_argument(
        "--image-output",
        type=Path,
        default=Path("satellite-smoke-bavaria.png"),
    )
    return parser.parse_args()


def main() -> None:
    args = arguments()
    try:
        report = asyncio.run(run(args))
    except Exception as exc:
        report = {
            "status": "FAIL",
            "errorType": type(exc).__name__,
            "error": str(exc),
            "product": args.product,
            "maximumAllowedAgeMinutes": args.maximum_age_minutes,
            "minimumProducts": args.minimum_products,
            "generatedAt": generated_at(),
        }
        write_report(args.report_output, report)
        print(json.dumps(report, ensure_ascii=False))
        raise
    write_report(args.report_output, report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
