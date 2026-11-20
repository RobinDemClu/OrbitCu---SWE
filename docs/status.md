# Status and roadmap

Last updated: 20 November 2026 (morning).

## Done

| Area | What exists | Where |
|---|---|---|
| Proposal and requirements | Phase 2 proposal (8 requirements, architecture, team roles) | course deliverable |
| Smelter registry | 29 primary smelters, ~11.7 Mt/yr (~47 % of world capacity), each with operator evidence graded A / B / C; 8 exclusions documented | `data/registry/`, [report, Part II section 3](latex/orbitcu_report.pdf) |
| Grade-C satellite check | 10 sites without operator confirmation checked with VIIRS and Sentinel-2: 8 active, 2 likely active | `c_site_activity_check.csv` |
| Geolocation | furnace points from VIIRS clusters + Sentinel-2 hot pixels: 10 high, 5 medium, 14 low confidence | `smelter_sites.csv`, `orbitcu.geolocate` |
| Ingestion: optical | Sentinel-2 L2A and Landsat 8/9 with server-side crop and quality fractions | `orbitcu.ingest.optical` |
| Ingestion: VIIRS | NASA FIRMS SP + NRT with quota back-off | `orbitcu.ingest.firms` |
| Ingestion: SO2 and winds | Sentinel-5P and ERA5 now download end to end (download hosts unblocked on 20 Nov 2026); multi-site S5P backfill `orbitcu s5p-batch` | `orbitcu.ingest.s5p`, `orbitcu.ingest.era5` |
| Point-in-time rules | `acquired_at` / `available_at` / `pit_basis` on every record | `orbitcu.ingest.pit` |
| Prototype | one year (Nov 2025 - Oct 2026) on Glogow, Hamburg, Kamoa-Kakula: 576 optical scenes, no failure | [`prototype_results.md`](prototype_results.md) |
| Status map | `orbitcu statusmap --open` downloads the VIIRS history and builds an interactive map of the smelters with monthly activity status | `orbitcu.statusmap` |
| Tests | 20 unit tests, no network | `tests/` |

## Remaining

### Short term (unblock the backfill)

1. Place the **14 low-confidence sites** by hand on a map (Kamoa-Kakula first:
   its position is still the prior).
2. Decide **slot 30** of the registry (Southwest Copper Kunming or Tongling
   Jinxin) and fill Manyar's capacity.
3. Run the **Sentinel-5P and ERA5** backfill for all sites (hosts now
   allowed; first SO2 emission fit in progress).
4. Review and merge the ingestion pull request into `main`.
5. Run the **backfill 2018 -> today** for all sites (~60 000 optical scenes,
   about 10 h from outside Azure), then set up incremental updates.

### Pipeline stages still to build

| Stage | Work |
|---|---|
| 3. Thermal detection | hotspot score per site and acquisition from Sentinel-2, Landsat and VIIRS, with quality flag |
| 4. SO2 emissions | emission estimate per overpass from S5P columns and ERA5 winds (Fioletov-style fit) |
| 5. Fusion | state-space model giving a daily probability active per site, robust to cloud gaps |
| 6. Indices | capacity-weighted global and regional activity indices |
| 7. Validation and nowcast | known-event catalogue, comparison with official statistics and SAVANT figures; monthly production nowcast |
| Market data | futures prices, inventories, official statistics with publication dates (LSEG Workspace) |
| 8. Backtest | walk-forward strategies with rolls and transaction costs, performance and risk metrics |
| 9. Dashboard | full web dashboard (map, site series, indices, nowcasts, backtest) |
| Infrastructure | Kubernetes on Azure, workflows, storage, CI/CD |

### Open decisions

* Exchange and contract for the backtest (LME, COMEX or SHFE).
* Non-functional requirements (latency, availability, reproducibility).
* Tools: workflow engine, storage format, experiment tracking, dashboard framework.
* Source of the known-event ground truth.
* Task distribution beyond the mid-term (postponed to the design phase).
* Project calendar and deadlines.
