# DWD source verification: current RV and WN HDF5 products

Verification date: 2026-08-05 UTC

This document separates observed source facts from application decisions. The
live smoke test was used only to verify current file structure. It did not
produce or authorize a safety state.

## Live smoke test result

Workflow run:

```text
DWD Live Source Smoke / run 30981576132
result: success
```

The workflow downloaded both current aliases, validated and extracted the TAR
archives, opened HDF5 members with `h5py`, and uploaded a JSON report.

### RV archive

```text
alias: composite_rv_LATEST.tar
archive SHA-256: 2e3a5103cd9fb4f4b0d13b1dc43fd93509f8f9e9dfeb87a38ed6e96a75a5d420
archive bytes: 1,884,160
reference cycle: 2026-08-05 06:25 UTC
members: 25
lead times: 000, 005, ..., 120 minutes
```

### WN archive

The single-underscore alias returned HTTP 404. The current observed alias with
two underscores succeeded:

```text
alias: composite_wn__LATEST.tar
archive SHA-256: 8251d47b54ce35c227d53c68fdfbde34462a20035b7465c176bd6e1ca6095cf1
archive bytes: 1,853,440
reference cycle: 2026-08-05 06:25 UTC
members: 25
lead times: 000, 005, ..., 120 minutes
```

The resolver therefore retains both WN alias candidates and a timestamped-file
fallback.

## Observed ODIM HDF5 structure

Both products used:

```text
root Conventions: ODIM_H5/V2_3
root /what version: H5rad 2.3
shape: 1200 rows x 1100 columns
cell size: 1000 m x 1000 m
compression: gzip
```

Required observed paths:

```text
/what
/where
/how
/dataset1/what
/dataset1/how
/dataset1/data1/what
/dataset1/data1/data
```

Observed projection:

```text
+proj=stere +lat_ts=60 +lat_0=90 +lon_0=10
+x_0=543196.83521776402 +y_0=3622588.8619310022
+units=m +a=6378137 +b=6356752.3142451802 +no_defs
```

The published geographic outer corners transform to approximately:

```text
left edge:       -500 m
right edge:   1,099,500 m
top edge:         500 m
bottom edge: -1,199,500 m
```

This confirms that row 0 is at the northern/top edge and column 0 at the
western/left edge. Pixel indices are derived from transformed outer edges and
cell size; they are not guessed from a hard-coded site pixel.

## RV metadata

Observed initial member:

```text
member: composite_rv_20260805_0625_000-hd5
dtype: uint32
quantity: ACRR
gain: 0.0009999999317806213
offset: -0.0009999999317806213
nodata: 4294967295
undetect: 0
start: 2026-08-05 06:20:00 UTC
end: 2026-08-05 06:25:00 UTC
simulated: False
```

Observed +120-minute member ended at 08:25 UTC and carried
`simulated: True`.

The application labels RV as a five-minute amount in `mm/5min` according to
the current DWD product documentation. It keeps the original amount separate
from any later derived hourly equivalent.

## WN metadata

Observed initial member:

```text
member: composite_wn_20260805_0625_000-hd5
dtype: uint16
quantity: DBZH
gain: 0.002929821616590115
offset: -64.00292982161659
nodata: 65535
undetect: 0
start: 2026-08-05 06:25:03 UTC
end: 2026-08-05 06:25:34 UTC
simulated: False
```

WN remains reflectivity in `dBZ`. It is not converted into a quantitative rain
amount by the core parser.

## Parser invariants implemented from the observation

The HDF5 reader now rejects a frame unless all relevant conditions hold:

- filename matches the current canonical product/member convention,
- filename product and requested product agree,
- root convention is exactly the verified ODIM version,
- filename and root reference timestamps agree,
- lead time and valid end time agree within a small publication tolerance,
- observation/forecast simulation flag is consistent with lead time,
- product quantity is `ACRR` for RV or `DBZH` for WN,
- gain, offset and sentinels are finite and plausible,
- dimensions, dataset shape and unsigned dtype agree,
- projected corner extent agrees with dimensions and scale,
- location transforms inside valid raster bounds.

`nodata`, `undetect`, and valid values remain three distinct states.

## Geiselhöring projection check

Using the configured approximate coordinate:

```text
latitude: 48.84
longitude: 12.40
```

the verified projection yields approximately:

```text
x: 730,661 m
y: -850,177 m
full-resolution pixel: row 850, column 731
```

The value is calculated at runtime from metadata and coordinates. It is not
stored as a fixed pixel constant.

## Remaining gate before runtime source state can become LIVE

The following still has to be implemented and tested before RV can influence a
real safety decision:

- complete archive-cycle consistency check across all 25 members,
- actual raster decoding for all lead times,
- coverage and neighborhood evaluation,
- conservative weak-rain and isolated-pixel rules,
- arrival-window derivation,
- source age and publication-delay handling,
- atomic snapshot publication,
- scheduler integration.

Until these gates pass, RV and CAP remain non-green core sources.

## Official references

- DWD radar HDF5 migration announcement and product naming:
  `https://www.dwd.de/DE/leistungen/opendata/neuigkeiten/opendata_august2025_1.html`
- DWD radar product overview:
  `https://www.dwd.de/DE/leistungen/radarprodukte/radarprodukte.html`
- RV service profile:
  `https://www.dwd.de/DE/leistungen/radarprodukte/radarkomposit_rv_hdf5.pdf`
- WN service profile:
  `https://www.dwd.de/DE/leistungen/radarprodukte/radarkomposit_wn_hdf5.pdf`
