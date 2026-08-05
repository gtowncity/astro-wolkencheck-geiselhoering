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


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def red_pixel_count(content: bytes) -> int:
    image = Image.open(io.BytesIO(content)).convert("RGB")
    return sum(
        1
        for red, green, blue in image.getdata()
        if red > 180 and green < 90 and blue < 110
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="awc-satellite-smoke-") as temporary:
        service = SatelliteImageService(Path(temporary))
        metadata = await service.metadata(args.product)
        products = metadata.get("products")
        frames = metadata.get("frames")
        if not isinstance(products, list):
            raise RuntimeError("Satellite metadata has no product list")
        if not isinstance(frames, list) or not frames:
            raise RuntimeError("Selected satellite product has no real frames")

        available = [item for item in products if item.get("available") is True]
        if len(available) < args.minimum_products:
            raise RuntimeError(
                f"Only {len(available)} EUMETSAT products are available; "
                f"expected at least {args.minimum_products}"
            )

        latest = parse_time(str(frames[-1]))
        age_minutes = max(
            0.0,
            (datetime.now(UTC) - latest).total_seconds() / 60,
        )
        if age_minutes > args.maximum_age_minutes:
            raise RuntimeError(
                f"Latest real EUMETSAT frame is {age_minutes:.1f} minutes old; "
                f"limit is {args.maximum_age_minutes} minutes"
            )

        result = await service.render(
            product_key=args.product,
            observed_at=latest,
            latitude=args.latitude,
            longitude=args.longitude,
            location_name=args.location_name,
        )
        image = Image.open(io.BytesIO(result.png))
        image.verify()
        red_pixels = red_pixel_count(result.png)
        if red_pixels < 20:
            raise RuntimeError("Rendered real satellite frame has no visible red site pin")

        args.image_output.parent.mkdir(parents=True, exist_ok=True)
        args.image_output.write_bytes(result.png)
        report: dict[str, Any] = {
            "status": "PASS",
            "provider": metadata.get("provider"),
            "satellite": metadata.get("satellite"),
            "selectedProduct": metadata.get("selectedProduct"),
            "availableProducts": [
                {
                    "key": item.get("key"),
                    "title": item.get("title"),
                    "latestTime": item.get("latestTime"),
                    "latestAgeMinutes": item.get("latestAgeMinutes"),
                    "fresh": item.get("fresh"),
                    "frameCount": item.get("frameCount"),
                }
                for item in available
            ],
            "selectedFrameCount": len(frames),
            "latestFrame": latest.isoformat().replace("+00:00", "Z"),
            "latestAgeMinutes": round(age_minutes, 2),
            "maximumAllowedAgeMinutes": args.maximum_age_minutes,
            "imageSize": list(Image.open(io.BytesIO(result.png)).size),
            "redPinPixels": red_pixels,
            "cache": "HIT" if result.cached else "MISS",
            "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
    report = asyncio.run(run(args))
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
