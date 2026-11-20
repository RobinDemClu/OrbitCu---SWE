# Architecture

## Pipeline stages

```
 1 Registry ──> 2 Ingestion ──┬─> 3 Thermal hotspots ─┐
                              └─> 4 SO2 emissions ────┴─> 5 Fusion ──> 6 Indices
                                                                         │
            9 Dashboard <── 8 Backtest <── 7 Validation & nowcast <──────┘
                                  ^
                     market data (prices, inventories, official stats)
```

| # | Stage | Input | Output | State |
|---|---|---|---|---|
| 1 | Smelter registry | operator documents | `data/registry/*.csv` | done (29 of 30) |
| 2 | Satellite ingestion | registry, satellite APIs | per-site Parquet manifests + GeoTIFF clips | optical + VIIRS done; S5P / ERA5 written, not run |
| 3 | Thermal hotspot detection | Sentinel-2, Landsat, VIIRS | hotspot score per site and acquisition | first rule only (TAI hot pixels) |
| 4 | SO2 emission estimation | Sentinel-5P, ERA5 winds | emission estimate per site and overpass | to do |
| 5 | Multi-sensor fusion | outputs of 3 and 4 | daily probability active per site | to do |
| 6 | Activity indices | output of 5, capacities | global / regional capacity-weighted indices | to do |
| 7 | Validation and nowcast | indices, events, official stats | validation report, monthly nowcast | to do |
| 8 | Backtest | indices, nowcast, futures data | strategy performance and risk | to do |
| 9 | Dashboard | everything above | web pages | first status map only |

## Design principles

* **One reference for sites.** Every stage reads site ids, coordinates and
  areas of interest from `data/registry/smelter_sites.csv` through
  `orbitcu.sites.load_sites()`.
* **Download only the area of interest.** Each site has a 3 x 3 km thermal box
  and a 50 km SO2 disc, computed in the local UTM zone. Optical scenes are
  cropped on the server (Planetary Computer data API), about 90 kB per
  Sentinel-2 scene instead of hundreds of megabytes.
* **Point-in-time everywhere.** Every record carries `acquired_at` (when the
  satellite saw it), `available_at` (when a trader could have had it) and
  `pit_basis` (how `available_at` was obtained). `orbitcu.ingest.pit.usable()`
  filters a table to what was knowable at a given decision time.
* **Quality travels with the data.** Each optical record stores cloud, shadow
  and no-data fractions over the area of interest, so later stages can weight
  or drop observations instead of losing them silently.
* **Free data only.** All sources are free; cloud processing is covered by the
  team's Azure for Students credits.

## Target infrastructure (planned)

The proposal targets a Kubernetes cluster in the same Azure region as the
Microsoft Planetary Computer (West Europe), event-driven workflows with
autoscaling, a file-based analytical store and experiment tracking. The tools
(workflow engine, storage format, tracking tool, dashboard framework) are not
chosen yet. The current code runs as a plain Python package from a laptop or a
cloud container.
