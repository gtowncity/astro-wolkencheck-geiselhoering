"""Strict, metadata-driven reader for current DWD RV/WN ODIM HDF5 frames."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import h5py
import numpy as np
from pyproj import CRS, Transformer


class RadarHdf5Error(RuntimeError):
    """Raised when a radar frame is structurally or semantically invalid."""


class PixelState(StrEnum):
    VALID = "VALID"
    UNDETECT = "UNDETECT"
    NODATA = "NODATA"


@dataclass(frozen=True, slots=True)
class DecodedPixel:
    state: PixelState
    raw_value: int
    physical_value: float | None


@dataclass(frozen=True, slots=True)
class GridIndex:
    row: int
    column: int


@dataclass(frozen=True, slots=True)
class RadarFrameMetadata:
    product: str
    member_name: str
    conventions: str
    reference_time: datetime
    lead_minutes: int
    interval_start: datetime
    interval_end: datetime
    simulated: bool
    quantity: str
    unit: str
    gain: float
    offset: float
    nodata: int
    undetect: int
    shape: tuple[int, int]
    dtype: str
    projection: str
    xscale_m: float
    yscale_m: float
    left_edge_m: float
    right_edge_m: float
    top_edge_m: float
    bottom_edge_m: float

    def index_for_lonlat(self, longitude: float, latitude: float) -> GridIndex:
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("Longitude or latitude is outside its valid range")
        transformer = Transformer.from_crs(
            "EPSG:4326", CRS.from_user_input(self.projection), always_xy=True
        )
        x, y = transformer.transform(longitude, latitude)
        if not (math.isfinite(x) and math.isfinite(y)):
            raise RadarHdf5Error("Coordinate transformation returned a non-finite value")
        if not (
            self.left_edge_m <= x < self.right_edge_m and self.bottom_edge_m < y <= self.top_edge_m
        ):
            raise RadarHdf5Error("Location is outside the radar raster")
        column = math.floor((x - self.left_edge_m) / self.xscale_m)
        row = math.floor((self.top_edge_m - y) / self.yscale_m)
        if not (0 <= row < self.shape[0] and 0 <= column < self.shape[1]):
            raise RadarHdf5Error("Calculated location index is outside the radar raster")
        return GridIndex(row=row, column=column)

    def decode(self, raw_value: int) -> DecodedPixel:
        if raw_value == self.nodata:
            return DecodedPixel(PixelState.NODATA, raw_value, None)
        if raw_value == self.undetect:
            return DecodedPixel(PixelState.UNDETECT, raw_value, None)
        value = raw_value * self.gain + self.offset
        if not math.isfinite(value):
            raise RadarHdf5Error("Decoded radar value is not finite")
        return DecodedPixel(PixelState.VALID, raw_value, value)


@dataclass(frozen=True, slots=True)
class RadarFrame:
    metadata: RadarFrameMetadata
    data: np.ndarray[Any, np.dtype[np.integer[Any]]]

    def value_at(self, longitude: float, latitude: float) -> DecodedPixel:
        index = self.metadata.index_for_lonlat(longitude, latitude)
        return self.metadata.decode(int(self.data[index.row, index.column]))


_MEMBER_PATTERN = re.compile(
    r"^composite_(?P<product>rv|wn)_(?P<date>\d{8})_(?P<time>\d{4})_"
    r"(?P<lead>\d{3})-hd5$"
)
_EXPECTED_QUANTITY = {"DWD_RV": "ACRR", "DWD_WN": "DBZH"}
_PRODUCT_UNIT = {"DWD_RV": "mm/5min", "DWD_WN": "dBZ"}
_MAX_ROWS = 2000
_MAX_COLUMNS = 2000
_MAX_ELEMENTS = 4_000_000


def _text(value: Any, name: str) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="strict")
    if not isinstance(value, str) or not value:
        raise RadarHdf5Error(f"Attribute {name} is missing or not text")
    return value


def _finite_number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RadarHdf5Error(f"Attribute {name} is not numeric") from exc
    if not math.isfinite(number):
        raise RadarHdf5Error(f"Attribute {name} is not finite")
    return number


def _integer(value: Any, name: str) -> int:
    number = _finite_number(value, name)
    if not number.is_integer():
        raise RadarHdf5Error(f"Attribute {name} is not an integer")
    return int(number)


def _timestamp(date_value: Any, time_value: Any, prefix: str) -> datetime:
    date_text = _text(date_value, f"{prefix}date")
    time_text = _text(time_value, f"{prefix}time")
    try:
        return datetime.strptime(date_text + time_text, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError as exc:
        raise RadarHdf5Error(f"Invalid {prefix} timestamp") from exc


def _bool_text(value: Any, name: str) -> bool:
    normalized = _text(value, name).strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise RadarHdf5Error(f"Attribute {name} is not a boolean string")


def _required_group(handle: h5py.File, path: str) -> h5py.Group:
    item = handle.get(path)
    if not isinstance(item, h5py.Group):
        raise RadarHdf5Error(f"Required HDF5 group is missing: {path}")
    return item


def _required_dataset(handle: h5py.File, path: str) -> h5py.Dataset:
    item = handle.get(path)
    if not isinstance(item, h5py.Dataset):
        raise RadarHdf5Error(f"Required HDF5 dataset is missing: {path}")
    return item


def _parse_member_name(name: str) -> tuple[str, datetime, int]:
    match = _MEMBER_PATTERN.fullmatch(name)
    if match is None:
        raise RadarHdf5Error("Radar member name does not match the current HDF5 convention")
    product = "DWD_RV" if match.group("product") == "rv" else "DWD_WN"
    reference_time = datetime.strptime(
        match.group("date") + match.group("time"), "%Y%m%d%H%M"
    ).replace(tzinfo=UTC)
    lead_minutes = int(match.group("lead"))
    if lead_minutes < 0 or lead_minutes > 240 or lead_minutes % 5:
        raise RadarHdf5Error("Radar lead time is outside the supported five-minute grid")
    return product, reference_time, lead_minutes


def _projected_edges(where: h5py.Group, projection: str) -> tuple[float, float, float, float]:
    transformer = Transformer.from_crs("EPSG:4326", CRS.from_user_input(projection), always_xy=True)
    left, top = transformer.transform(
        _finite_number(where.attrs.get("UL_lon"), "UL_lon"),
        _finite_number(where.attrs.get("UL_lat"), "UL_lat"),
    )
    right, bottom = transformer.transform(
        _finite_number(where.attrs.get("LR_lon"), "LR_lon"),
        _finite_number(where.attrs.get("LR_lat"), "LR_lat"),
    )
    if not all(math.isfinite(value) for value in (left, right, top, bottom)):
        raise RadarHdf5Error("Projected radar corners are not finite")
    if not (right > left and top > bottom):
        raise RadarHdf5Error("Radar corner orientation is invalid")
    return left, right, top, bottom


def load_radar_frame(path: Path, *, expected_product: str | None = None) -> RadarFrame:
    member_name = path.name
    product, filename_reference, lead_minutes = _parse_member_name(member_name)
    if expected_product is not None and product != expected_product:
        raise RadarHdf5Error(f"Expected {expected_product}, but member name identifies {product}")

    try:
        with h5py.File(path, "r") as handle:
            conventions = _text(handle.attrs.get("Conventions"), "Conventions")
            if conventions != "ODIM_H5/V2_3":
                raise RadarHdf5Error(f"Unsupported HDF5 convention: {conventions}")

            root_what = _required_group(handle, "/what")
            root_reference = _timestamp(
                root_what.attrs.get("date"), root_what.attrs.get("time"), "reference-"
            )
            if root_reference != filename_reference:
                raise RadarHdf5Error("Filename and root reference timestamps disagree")

            dataset_what = _required_group(handle, "/dataset1/what")
            interval_start = _timestamp(
                dataset_what.attrs.get("startdate"),
                dataset_what.attrs.get("starttime"),
                "start-",
            )
            interval_end = _timestamp(
                dataset_what.attrs.get("enddate"),
                dataset_what.attrs.get("endtime"),
                "end-",
            )
            if interval_end <= interval_start:
                raise RadarHdf5Error("Radar accumulation interval is not positive")
            expected_valid_minute = filename_reference.timestamp() + lead_minutes * 60
            if abs(interval_end.timestamp() - expected_valid_minute) > 90:
                raise RadarHdf5Error("Lead time and HDF5 valid time disagree")

            how = _required_group(handle, "/how")
            simulated = _bool_text(how.attrs.get("simulated"), "simulated")
            if (lead_minutes == 0 and simulated) or (lead_minutes > 0 and not simulated):
                raise RadarHdf5Error("Observed/forecast simulation flag is inconsistent")

            data_what = _required_group(handle, "/dataset1/data1/what")
            quantity = _text(data_what.attrs.get("quantity"), "quantity")
            if quantity != _EXPECTED_QUANTITY[product]:
                raise RadarHdf5Error(f"Unexpected quantity {quantity!r} for product {product}")
            gain = _finite_number(data_what.attrs.get("gain"), "gain")
            offset = _finite_number(data_what.attrs.get("offset"), "offset")
            nodata = _integer(data_what.attrs.get("nodata"), "nodata")
            undetect = _integer(data_what.attrs.get("undetect"), "undetect")
            if gain <= 0 or nodata == undetect:
                raise RadarHdf5Error("Radar scaling or sentinel values are invalid")

            where = _required_group(handle, "/where")
            projection = _text(where.attrs.get("projdef"), "projdef")
            xsize = _integer(where.attrs.get("xsize"), "xsize")
            ysize = _integer(where.attrs.get("ysize"), "ysize")
            xscale = _finite_number(where.attrs.get("xscale"), "xscale")
            yscale = _finite_number(where.attrs.get("yscale"), "yscale")
            if not (0 < xsize <= _MAX_COLUMNS and 0 < ysize <= _MAX_ROWS):
                raise RadarHdf5Error("Radar dimensions are outside safety limits")
            if xsize * ysize > _MAX_ELEMENTS or xscale <= 0 or yscale <= 0:
                raise RadarHdf5Error("Radar grid size or scale is invalid")
            left, right, top, bottom = _projected_edges(where, projection)
            if not math.isclose(right - left, xsize * xscale, abs_tol=xscale * 0.05):
                raise RadarHdf5Error("Projected radar width does not match metadata")
            if not math.isclose(top - bottom, ysize * yscale, abs_tol=yscale * 0.05):
                raise RadarHdf5Error("Projected radar height does not match metadata")

            dataset = _required_dataset(handle, "/dataset1/data1/data")
            if dataset.ndim != 2 or dataset.shape != (ysize, xsize):
                raise RadarHdf5Error("Radar dataset shape disagrees with grid metadata")
            if dataset.dtype.kind != "u":
                raise RadarHdf5Error("Radar dataset must use an unsigned integer type")
            dtype_max = int(np.iinfo(dataset.dtype).max)
            if not (0 <= nodata <= dtype_max and 0 <= undetect <= dtype_max):
                raise RadarHdf5Error("Radar sentinel is outside the dataset dtype")
            data = np.asarray(dataset[...])
    except OSError as exc:
        raise RadarHdf5Error(f"HDF5 file could not be opened: {path.name}") from exc

    metadata = RadarFrameMetadata(
        product=product,
        member_name=member_name,
        conventions=conventions,
        reference_time=root_reference,
        lead_minutes=lead_minutes,
        interval_start=interval_start,
        interval_end=interval_end,
        simulated=simulated,
        quantity=quantity,
        unit=_PRODUCT_UNIT[product],
        gain=gain,
        offset=offset,
        nodata=nodata,
        undetect=undetect,
        shape=(ysize, xsize),
        dtype=str(data.dtype),
        projection=projection,
        xscale_m=xscale,
        yscale_m=yscale,
        left_edge_m=left,
        right_edge_m=right,
        top_edge_m=top,
        bottom_edge_m=bottom,
    )
    return RadarFrame(metadata=metadata, data=data)
