# Setup and usage

## Requirements

* Python 3.10 or later
* Network access to `planetarycomputer.microsoft.com` and
  `firms.modaps.eosdis.nasa.gov`; for SO2 and winds also
  `sentinel5euwest.blob.core.windows.net`, `cds.climate.copernicus.eu` and
  `object-store.os-api.cci2.ecmwf.int`

## Installation

```bash
git clone https://github.com/RobinDemClu/OrbitCu---SWE.git
cd OrbitCu---SWE
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev,era5,s5p]"
```

Extras: `era5` (cdsapi, xarray, netCDF4), `s5p` (xarray, h5netcdf, fsspec),
`dev` (pytest).

## API keys

| Variable | Where to get it |
|---|---|
| `FIRMS_MAP_KEY` | free, https://firms.modaps.eosdis.nasa.gov/api/map_key/ |
| `CDSAPI_KEY` | Copernicus CDS account, personal access token; accept the ERA5 licence once |

The Planetary Computer needs no key. Keys can be put in a `.env` file, which
is git-ignored. Never commit them.

## Command line

```bash
orbitcu optical --sensor sentinel2 --sites PL-GLOGOW --start 2025-11-01 --end 2026-10-31
orbitcu optical --sensor landsat   --sites PL-GLOGOW --start 2025-11-01 --end 2026-10-31
orbitcu firms   --sites PL-GLOGOW DE-HAMBURG --start 2025-11-01 --end 2026-10-31
orbitcu s5p     --sites DE-HAMBURG --start 2026-06-01 --end 2026-06-30
orbitcu era5    --sites DE-HAMBURG --start 2025-11-01 --end 2026-10-31
```

`--sites` takes registry ids (see `data/registry/smelter_sites.csv`); without
it, all sites are processed. `--out` changes the output folder (default
`output/`).

Outputs, per site: one Parquet manifest per source (with `acquired_at`,
`available_at`, `pit_basis` and quality fractions) and GeoTIFF clips of the
thermal area for the optical sensors.

## Status map

```bash
python -m orbitcu.statusmap --firms-dir cache/firms_hist --start 2025-01 --end 2026-10 \
    --out output/status_map.html
```

Builds one self-contained HTML page: the smelters on a world map, sized by
capacity, with a monthly VIIRS activity status over a 1, 3 or 12-month window.

## Tests

```bash
pytest
```

20 unit tests, no network access needed.

## Conventions

* Work happens on the `developpement` branch; changes reach `main` through
  pull requests.
* Generated data (`output/`, `cache/`) is never committed.
