# Package reference (`orbitcu`)

## `orbitcu.sites`

* `Site`: one smelter (id, name, coordinates, capacity) with its areas of
  interest: `thermal_bbox()` (square, half-side 1.5 km) and `so2_bbox()` /
  SO2 disc (radius 50 km), both built in the local UTM zone.
* `load_sites(path=None, ids=None)`: reads `data/registry/smelter_sites.csv`.
* `utm_crs(lat, lon)`: UTM projection of a point.

## `orbitcu.ingest`

| Module | Main functions | Role |
|---|---|---|
| `optical` | `search_scenes`, `fetch_clip`, `quality_fractions`, `ingest_site`, `read_clip` | Sentinel-2 and Landsat 8/9: STAC search, server-side crop, cloud / shadow / no-data fractions, Parquet manifest + GeoTIFF clips |
| `firms` | `fetch_area`, `plan_requests`, `normalise`, `data_availability` | NASA FIRMS VIIRS detections, SP archive then NRT, quota back-off |
| `s5p` | `search_orbits`, `extract_orbit`, `processing_time` | Sentinel-5P SO2 pixels inside the 50 km disc, with `qa_value` |
| `era5` | `request`, `retrieve`, `to_table` | ERA5 winds and boundary-layer height around the site |
| `pit` | `available_at`, `usable`, `utc` | point-in-time rules: when each record became available, and filtering at a decision time |

Default latencies used when a provider timestamp is missing: FIRMS NRT 3 h,
S5P NRTI 3 h / OFFL 5 days, ERA5T 5 days, Sentinel-2 1 day, Landsat 16 days.

## `orbitcu.geolocate`

* `clusters(df, prior_lat, prior_lon, cluster_km=1.5)`: persistent VIIRS heat
  clusters around a prior position.
* `hot_pixels(b8a, b11, b12)`: thermal anomaly index (TAI) test.
* `s2_hot_pixel_persistence(...)`: share of clear Sentinel-2 scenes on which
  each pixel is hot.

## `orbitcu.activity_check`

* `monthly_detection_days(df, lat, lon, radius_km)`: distinct VIIRS detection
  days per month near a furnace.
* `verdict(monthly)`: active / intermittent / inactive from recent months.

## `orbitcu.statusmap`

* `typical_level(days)`: a site's usual monthly detection level (median of
  months with at least one detection).
* `build_data(...)`, `render(...)`: build the self-contained status map page
  from cached FIRMS data.

## `orbitcu.cli`

Entry point of the `orbitcu` command: `firms`, `optical`, `s5p`, `era5`.
