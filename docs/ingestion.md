# FR-2 Satellite ingestion

Status: prototype (November 2026). Sentinel-2, Landsat 8/9 and VIIRS (FIRMS) run
end to end; Sentinel-5P and ERA5 code is written and unit-tested but blocked by
the cloud environment's network policy (see *Known blockers*).

## 1. Sites and areas of interest

`data/registry/smelter_sites.csv` holds one row per smelter of the FR-1 registry
(29 sites, slot 30 open). Each site is a furnace point plus two derived AOIs,
computed in the local UTM zone (`orbitcu.sites.Site`):

| AOI | Geometry | Used for |
|---|---|---|
| `point` | furnace / converter aisle | reference |
| `thermal` | square, half-side 1.5 km | Sentinel-2, Landsat, VIIRS |
| `so2` | disc, radius 50 km | Sentinel-5P SO2, ERA5 wind box |

### Geolocation method

No web gazetteer is reachable from the build environment (OpenStreetMap,
Wikipedia and Wikidata are blocked), so every coordinate was derived from the
satellite data themselves:

1. **Prior**: town / street-address level position (`site_priors.csv`).
2. **VIIRS persistent clusters** (`geolocate.clusters`): one year of VIIRS S-NPP
   standard-processing detections (FIRMS `VIIRS_SNPP_SP`, 2025-08-01 to
   2026-07-31) within +/-25 km; greedy density clustering at 1.5 km; for each
   cluster we keep detections, distinct days, share of FIRMS `type = 2`
   ("static land source") and distance to the prior.
3. **Sentinel-2 SWIR confirmation** (`geolocate.s2_hot_pixel_persistence`):
   10 clearest 2026 scenes, thermal anomaly index TAI = (B12 - B11) / B8A > 0.45
   (Marchese et al., 2019); count pixels hot on >= 25 % of clear scenes.
4. Analyst choice with a confidence grade:
   * **high** (10 sites): one dominant static cluster within ~6 km of the prior;
   * **medium** (5): a clear cluster but a competing industrial source nearby, or
     > 10 km from a county-level prior;
   * **low** (14): several candidate plants (Tongling, Chifeng, Huangshi), no
     cluster at all (Japan, Gresik, Kamoa), or only a town-level prior.

Low-confidence sites have `needs_review = True` and must be confirmed by a team
member on a map before the backfill. Steel works, refineries and power plants
are also persistent heat sources, so a cluster alone does not prove it is the
copper smelter.

## 2. Sources

| Source | Access | What we keep | `available_at` |
|---|---|---|---|
| Sentinel-2 L2A (10-20 m) | PC STAC + data API `item/bbox` | B8A, B11, B12, SCL clip | `s2:generation_time` |
| Landsat 8/9 C2 L2 (30 m) | PC STAC + data API `item/bbox` | nir08, swir16, swir22, lwir11, qa_pixel clip | STAC `created` |
| VIIRS 375 m (S-NPP, NOAA-20, NOAA-21) | NASA FIRMS area API | all detections in thermal box | acquisition + 3 h (NRT latency) |
| Sentinel-5P SO2 | PC STAC + HTTP range reads of the netCDF | pixels in 50 km disc, `qa_value` | processing time from file name |
| ERA5 single levels | Copernicus CDS API | u/v 10 m and 100 m, boundary-layer height | valid time + 5 days (ERA5T) |

Only the site's AOI is downloaded. The Planetary Computer data API (TiTiler)
crops the Cloud-Optimised GeoTIFFs server side and returns the AOI in the
scene's native UTM grid (`dst_crs`, nearest resampling, box snapped to the
pixel grid), ~90 kB per Sentinel-2 scene.

## 3. Point-in-time metadata

Every record carries `acquired_at`, `available_at`, `pit_basis` and
`ingested_at` (UTC). `orbitcu.ingest.pit.usable(df, t)` returns what a trader
could have known at time `t`. Rules:

* provider timestamp when it exists and is not before acquisition;
* otherwise a conservative latency (FIRMS NRT 3 h, S5P NRTI 3 h / OFFL 5 days,
  ERA5T 5 days, Sentinel-2 1 day, Landsat 16 days).

Approximations, to be revisited for the backtest:

* Sentinel-2 on the Planetary Computer is the reprocessed baseline 05.xx, so
  `s2:generation_time` can be later than the original release (conservative).
* FIRMS SP detections are stamped with the NRT latency although the SP record
  itself is released months later; the NRT and SP detection sets differ
  slightly. For live use only NRT is used.
* ERA5T values are replaced by final ERA5 after 2-3 months.

Cloud handling: per scene we store the provider's scene cloud cover and the
AOI-level `aoi_cloud_frac`, `aoi_shadow_frac`, `aoi_nodata_frac` from SCL
(classes 8, 9, 10 / 3 / 0) or Landsat QA_PIXEL bits (1, 2, 3 / 4 / 0). Scenes
whose footprint does not fully contain the AOI are dropped; duplicate
acquisitions on overlapping tiles / WRS rows are removed.

## 4. Prototype (2025-11-01 to 2026-10-31)

See `docs/prototype_results.md` for the numbers per site.

## 5. Known blockers and limits

* `*.blob.core.windows.net` is blocked, so raw Planetary Computer files
  (Sentinel-5P netCDF) cannot be read; optical data go through the data API.
* CDS serves files from `object-store.os-api.cci2.ecmwf.int`, also blocked; the
  ERA5 request is accepted (licence OK) but cannot be downloaded.
* FIRMS allows 5000 transactions per 10 minutes; large boxes cost more.
  `firms._get` backs off for 60 s and retries when FIRMS refuses a request (HTTP 400 "Invalid MAP_KEY"); the `mapkey_status` counter is not a reliable gate.
* Data API throughput is ~1-2 scenes/s with 8 threads; a 7-year backfill of
  29 sites (~60 000 scenes) takes about 10 h from outside Azure.

## References

* Marchese, F. et al. (2019). A multi-channel algorithm for mapping volcanic
  thermal anomalies by means of Sentinel-2 MSI and Landsat-8 OLI data. *Remote
  Sensing* 11(23), 2876.
* Schroeder, W. et al. (2016). Active fire detection using Landsat-8/OLI data.
  *Remote Sensing of Environment* 185, 210-220.
* Schroeder, W. et al. (2014). The New VIIRS 375 m active fire detection data
  product. *Remote Sensing of Environment* 143, 85-96.
* Liu, Y. et al. (2018). Identifying industrial heat sources using time-series
  of the VIIRS Nightfire product. *Remote Sensing of Environment* 204, 347-365.
* Fioletov, V. et al. (2015, 2020). Global SO2 point-source emissions from
  OMI / TROPOMI. *GRL* 42; *ACP* 20.
* Hersbach, H. et al. (2020). The ERA5 global reanalysis. *QJRMS* 146, 1999-2049.
