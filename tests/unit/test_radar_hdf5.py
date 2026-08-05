from datetime import UTC, datetime
from pathlib import Path

import h5py
import numpy as np
import pytest

from nowcast_service.sources.radar_hdf5 import (
    PixelState,
    RadarHdf5Error,
    load_radar_frame,
)

PROJECTION = (
    "+proj=stere +lat_ts=60 +lat_0=90 +lon_0=10 "
    "+x_0=543196.83521776402 +y_0=3622588.8619310022 +units=m "
    "+a=6378137 +b=6356752.3142451802 +no_defs"
)


def make_frame(
    path: Path,
    *,
    product: str = "rv",
    lead: int = 0,
    convention: str = "ODIM_H5/V2_3",
    quantity: str | None = None,
) -> Path:
    reference = datetime(2026, 8, 5, 6, 25, tzinfo=UTC)
    valid_end = reference.timestamp() + lead * 60
    end = datetime.fromtimestamp(valid_end, tz=UTC)
    start = datetime.fromtimestamp(valid_end - 300, tz=UTC)
    dtype = np.uint32 if product == "rv" else np.uint16
    nodata = np.iinfo(dtype).max
    quantity = quantity or ("ACRR" if product == "rv" else "DBZH")
    gain = 0.001 if product == "rv" else 0.002929821616590115
    offset = -0.001 if product == "rv" else -64.00292982161659
    data = np.zeros((12, 11), dtype=dtype)
    data[8, 7] = 101
    data[0, 0] = nodata

    with h5py.File(path, "w") as handle:
        handle.attrs["Conventions"] = convention
        root_what = handle.create_group("what")
        root_what.attrs["date"] = "20260805"
        root_what.attrs["time"] = "062500"
        root_what.attrs["object"] = "COMP"
        root_what.attrs["version"] = "H5rad 2.3"

        how = handle.create_group("how")
        how.attrs["simulated"] = "True" if lead else "False"

        where = handle.create_group("where")
        where.attrs.update(
            {
                "projdef": PROJECTION,
                "xsize": 11,
                "ysize": 12,
                "xscale": 100000.0,
                "yscale": 100000.0,
                "UL_lon": 1.463301510256666,
                "UL_lat": 55.862087108249824,
                "LR_lon": 16.580869348598274,
                "LR_lat": 45.68460578137082,
            }
        )

        dataset = handle.create_group("dataset1")
        dataset_what = dataset.create_group("what")
        dataset_what.attrs["startdate"] = start.strftime("%Y%m%d")
        dataset_what.attrs["starttime"] = start.strftime("%H%M%S")
        dataset_what.attrs["enddate"] = end.strftime("%Y%m%d")
        dataset_what.attrs["endtime"] = end.strftime("%H%M%S")

        data1 = dataset.create_group("data1")
        data_what = data1.create_group("what")
        data_what.attrs.update(
            {
                "quantity": quantity,
                "gain": gain,
                "offset": offset,
                "nodata": float(nodata),
                "undetect": 0.0,
            }
        )
        data1.create_dataset("data", data=data)
    return path


def test_reads_actual_odim_metadata_and_locates_geiselhoering(tmp_path: Path) -> None:
    path = make_frame(tmp_path / "composite_rv_20260805_0625_000-hd5")

    frame = load_radar_frame(path, expected_product="DWD_RV")
    index = frame.metadata.index_for_lonlat(12.40, 48.84)
    pixel = frame.value_at(12.40, 48.84)

    assert frame.metadata.conventions == "ODIM_H5/V2_3"
    assert frame.metadata.quantity == "ACRR"
    assert frame.metadata.unit == "mm/5min"
    assert frame.metadata.reference_time == datetime(2026, 8, 5, 6, 25, tzinfo=UTC)
    assert frame.metadata.lead_minutes == 0
    assert frame.metadata.simulated is False
    assert index.row == 8
    assert index.column == 7
    assert pixel.state is PixelState.VALID
    assert pixel.physical_value == pytest.approx(0.1)


def test_forecast_frame_uses_filename_lead_and_simulation_flag(tmp_path: Path) -> None:
    path = make_frame(
        tmp_path / "composite_wn_20260805_0625_120-hd5", product="wn", lead=120
    )

    frame = load_radar_frame(path, expected_product="DWD_WN")

    assert frame.metadata.quantity == "DBZH"
    assert frame.metadata.unit == "dBZ"
    assert frame.metadata.lead_minutes == 120
    assert frame.metadata.interval_end == datetime(2026, 8, 5, 8, 25, tzinfo=UTC)
    assert frame.metadata.simulated is True


def test_nodata_undetect_and_valid_values_stay_distinct(tmp_path: Path) -> None:
    path = make_frame(tmp_path / "composite_rv_20260805_0625_000-hd5")
    frame = load_radar_frame(path)

    assert frame.metadata.decode(frame.metadata.nodata).state is PixelState.NODATA
    assert frame.metadata.decode(frame.metadata.undetect).state is PixelState.UNDETECT
    assert frame.metadata.decode(101).physical_value == pytest.approx(0.1)


def test_outside_location_is_rejected(tmp_path: Path) -> None:
    frame = load_radar_frame(make_frame(tmp_path / "composite_rv_20260805_0625_000-hd5"))

    with pytest.raises(RadarHdf5Error, match="outside"):
        frame.metadata.index_for_lonlat(-120, 0)


def test_unexpected_convention_quantity_and_product_are_rejected(tmp_path: Path) -> None:
    wrong_convention = make_frame(
        tmp_path / "composite_rv_20260805_0625_000-hd5",
        convention="ODIM_H5/V2_2",
    )
    with pytest.raises(RadarHdf5Error, match="Unsupported"):
        load_radar_frame(wrong_convention)

    wrong_quantity = make_frame(
        tmp_path / "composite_rv_20260805_0625_005-hd5",
        lead=5,
        quantity="DBZH",
    )
    with pytest.raises(RadarHdf5Error, match="Unexpected quantity"):
        load_radar_frame(wrong_quantity)

    valid = make_frame(tmp_path / "composite_wn_20260805_0625_000-hd5", product="wn")
    with pytest.raises(RadarHdf5Error, match="Expected DWD_RV"):
        load_radar_frame(valid, expected_product="DWD_RV")


def test_invalid_file_name_and_corrupt_file_are_rejected(tmp_path: Path) -> None:
    bad_name = make_frame(tmp_path / "unknown-hd5")
    with pytest.raises(RadarHdf5Error, match="member name"):
        load_radar_frame(bad_name)

    corrupt = tmp_path / "composite_rv_20260805_0625_000-hd5"
    corrupt.write_bytes(b"not hdf5")
    with pytest.raises(RadarHdf5Error, match="could not be opened"):
        load_radar_frame(corrupt)
