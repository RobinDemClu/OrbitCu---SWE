# OrbitCu

Open, reproducible pipeline that measures from free satellite data whether the
largest copper smelters are operating, to nowcast refined copper supply and
backtest copper futures strategies (CSE 406 term project, team CoPPernicus).

This repository currently holds the **smelter registry (FR-1)** and the
**satellite ingestion layer (FR-2)**. See [`docs/ingestion.md`](docs/ingestion.md).

## Layout

```
data/registry/
  smelter_registry.csv       FR-1 registry (29 smelters, operator evidence grades A/B/C)
  smelter_excluded.csv       closed / suspended plants left out
  site_priors.csv            town / address level starting coordinates
  smelter_sites.csv          geolocated furnace points + AOIs + evidence + confidence
  c_site_activity_check.csv  VIIRS activity check of grade-C sites
src/orbitcu/
  sites.py                   Site model, thermal box (1.5 km) and SO2 disc (50 km) in local UTM
  geolocate.py               VIIRS persistent-cluster geolocation, Sentinel-2 TAI hot pixels
  activity_check.py          monthly VIIRS detection days -> active / intermittent / inactive?
  ingest/pit.py              point-in-time availability rules (no look-ahead)
  ingest/optical.py          Sentinel-2 L2A + Landsat 8/9 L2 (Planetary Computer STAC + data API)
  ingest/firms.py            NASA FIRMS VIIRS (SP archive + NRT)
  ingest/s5p.py              Sentinel-5P TROPOMI SO2 (range reads of netCDF)
  ingest/era5.py             ERA5 winds + boundary-layer height (Copernicus CDS)
  statusmap.py               interactive map of smelters and monthly VIIRS activity status
  templates/                 map page template + Natural Earth 1:50m TopoJSON (public domain)
  cli.py                     `orbitcu firms|optical|s5p|era5 ...`
tests/                       unit tests (no network)
```

## Quick start

```bash
pip install -e ".[dev,era5,s5p]"
export FIRMS_MAP_KEY=...      # https://firms.modaps.eosdis.nasa.gov/api/map_key/
export CDSAPI_KEY=...         # CDS personal access token; accept the ERA5 licence once
pytest
orbitcu optical --sensor sentinel2 --sites PL-GLOGOW --start 2025-11-01 --end 2026-10-31
orbitcu firms   --sites PL-GLOGOW --start 2025-11-01 --end 2026-10-31
```

Outputs go to `output/<site_id>/` (git-ignored): Parquet manifests with
`acquired_at`, `available_at`, `pit_basis`, cloud/quality fractions, and
GeoTIFF clips of the thermal AOI.
