# Data sources

All sources are free. They are reached through three access platforms.

## Satellite data

| Data | Platform | Resolution / revisit | Used for | Access in code | State |
|---|---|---|---|---|---|
| Sentinel-2 L2A (B8A, B11, B12, SCL) | Microsoft Planetary Computer | 20 m, ~5 days | SWIR hot pixels at the furnace | `ingest/optical.py` | running |
| Landsat 8/9 Collection 2 L2 | Microsoft Planetary Computer | 30 m, ~8 days combined | SWIR + thermal band, cross-check | `ingest/optical.py` | running |
| VIIRS 375 m active fires (S-NPP, NOAA-20, NOAA-21) | NASA FIRMS | 375 m, nightly | night-time heat detections | `ingest/firms.py` | running |
| Sentinel-5P TROPOMI SO2 | Microsoft Planetary Computer | ~5.5 x 3.5 km, daily | SO2 plume above the site | `ingest/s5p.py` | written, blocked |
| ERA5 reanalysis (winds 10 m / 100 m, boundary-layer height) | Copernicus Climate Data Store | 0.25 deg, hourly | wind to turn SO2 columns into emissions | `ingest/era5.py` | written, blocked |

### Access notes

* **Planetary Computer**: STAC search with `pystac-client`; pixels are fetched
  through the data API (`/api/data/v1/item/bbox/...`), which crops the
  Cloud-Optimised GeoTIFF on the server in the scene's native grid.
* **NASA FIRMS**: area API with a free `MAP_KEY`. Standard-processing (SP)
  archive for older months, near-real-time (NRT) for recent ones. The quota is
  5000 transactions per 10 minutes; when it is exhausted the server answers
  HTTP 400 "Invalid MAP_KEY", and the client waits 60 s and retries.
* **Copernicus CDS**: `cdsapi` with a personal access token; the ERA5 licence
  must be accepted once on the CDS website.

### Known blockers

The cloud build environment cannot reach two download hosts:

* `sentinel5euwest.blob.core.windows.net` (Sentinel-5P netCDF files);
* `object-store.os-api.cci2.ecmwf.int` (CDS file downloads).

Both hosts must be allowed in the environment's network settings (or the code
run from a machine with normal internet access) before SO2 and wind data can
be ingested.

## Market and reference data (planned)

| Data | Candidate source | Note |
|---|---|---|
| Copper futures prices (LME / COMEX / SHFE) | LSEG Workspace (team access) | exchange and contract still to choose |
| Exchange inventories | LSEG Workspace, exchange websites | need publication dates |
| Official production statistics | ICSG, national statistics offices, USGS | need publication dates for point-in-time use |
| Known events (shutdowns, restarts) | operator reports, press | catalogue still to build |
| Public SAVANT figures | Earth-i press releases | benchmark only |

## References

* Marchese, F. et al. (2019). A multi-channel algorithm for mapping volcanic
  thermal anomalies by means of Sentinel-2 MSI and Landsat-8 OLI data.
  *Remote Sensing* 11(23), 2876.
* Schroeder, W. et al. (2014). The New VIIRS 375 m active fire detection data
  product. *Remote Sensing of Environment* 143, 85-96.
* Schroeder, W. et al. (2016). Active fire detection using Landsat-8/OLI data.
  *Remote Sensing of Environment* 185, 210-220.
* Liu, Y. et al. (2018). Identifying industrial heat sources using time-series
  of the VIIRS Nightfire product. *Remote Sensing of Environment* 204, 347-365.
* Fioletov, V. et al. (2015, 2020). Global SO2 point-source emissions from
  OMI / TROPOMI. *GRL* 42; *ACP* 20.
* Hersbach, H. et al. (2020). The ERA5 global reanalysis. *QJRMS* 146, 1999-2049.
