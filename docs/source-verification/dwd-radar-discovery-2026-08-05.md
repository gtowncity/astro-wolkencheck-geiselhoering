# DWD source verification: radar directory and archive discovery

Verified on 2026-08-05 against the official DWD Open Data directory indexes.
This document records only what was actually observed or documented. Binary
HDF5 metadata verification remains a separate gate before radar values can
influence the safety decision.

## RV

Official directory:

```text
https://opendata.dwd.de/weather/radar/composite/rv/
```

Observed current naming:

```text
composite_rv_YYYYMMDD_HHMM.tar
composite_rv_LATEST.tar
```

The same directory also contained older transitional files named like:

```text
DE1200_RVYYMMDDHHMM.tar.bz2
```

The v4.3 implementation deliberately selects only the canonical HDF5 TAR
family. A legacy file must not silently replace the verified HDF5 product.

Official DWD documentation describes RV as five-minute accumulated
precipitation amount in millimetres. Each canonical TAR contains multiple
forecast files named like:

```text
composite_rv_YYYYMMDD_HHMM_PPP-hd5
```

`PPP` is a forecast lead and must be read and checked rather than inferred from
archive order.

## WN

Official directory:

```text
https://opendata.dwd.de/weather/radar/composite/wn/
```

Observed current timestamped naming:

```text
composite_wn_YYYYMMDD_HHMM.tar
```

The directory exposed the unusual alias:

```text
composite_wn__LATEST.tar
```

with two underscores. The resolver therefore tries both the documented-looking
single-underscore alias and the currently observed double-underscore alias,
then falls back to the newest canonical timestamped file from the directory.

WN is a reflectivity composite. It must not be treated as a quantitative
precipitation amount without a separately documented conversion.

## Resolver safety rules

The implemented discovery layer:

- accepts only HTTPS on `opendata.dwd.de`,
- rejects credentials and non-standard ports,
- ignores off-host, parent and nested links,
- ranks aliases first but retains timestamped fallbacks,
- does not accept legacy RV/WN names as canonical HDF5 candidates,
- does not declare a candidate valid until download and archive validation pass.

## Archive safety rules

The initial TAR layer:

- never uses `extractall`,
- rejects absolute and parent paths,
- rejects nested paths, links and device entries,
- accepts only expected HDF5-style suffixes,
- limits entry count, per-entry size and total expanded size,
- writes through temporary files and atomic rename.

## Remaining verification gate

Before RV or WN can be marked `LIVE` or influence a safety state, a real current
archive still has to be downloaded in a controlled live smoke test and the
following must be recorded:

- archive SHA-256,
- member list and lead times,
- HDF5 signature,
- ODIM/product version,
- dimensions and axis orientation,
- CRS and georeferencing,
- gain, offset, unit, nodata and undetect values,
- coverage at Geiselhöring,
- consistency between filename and internal timestamps.

Until that gate succeeds, the runtime source remains `INITIALIZING`,
`NOT_AVAILABLE` or `FAILED`; it must never produce `GREEN`.

## Official references

- DWD radar HDF5 migration announcement and product naming:
  `https://www.dwd.de/DE/leistungen/opendata/neuigkeiten/opendata_august2025_1.html`
- DWD radar product overview:
  `https://www.dwd.de/DE/leistungen/radarprodukte/radarprodukte.html`
- RV service profile:
  `https://www.dwd.de/DE/leistungen/radarprodukte/radarkomposit_rv_hdf5.pdf`
- WN service profile:
  `https://www.dwd.de/DE/leistungen/radarprodukte/radarkomposit_wn_hdf5.pdf`
