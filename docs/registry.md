# Smelter registry

Files in `data/registry/`:

| File | Content |
|---|---|
| `smelter_registry.csv` | 29 primary smelters + one open slot, with capacity and operator evidence |
| `smelter_excluded.csv` | large plants left out (closed or suspended) and why |
| `site_priors.csv` | town / street-address level starting coordinates |
| `smelter_sites.csv` | final furnace coordinates, geolocation evidence and confidence, AOI sizes |
| `c_site_activity_check.csv` | satellite activity check of the grade-C sites |

## Selection rules

* **Primary smelters only** (not refineries), **active in 2026**.
* Each site is checked against the **operator's own published documents**,
  site by site, for both activity and capacity.
* Capacities are harmonised to blister / anode copper, the stage the
  satellites observe.
* Slot 30 is left open until the team decides. Candidates: Southwest Copper
  (Kunming, Yunnan Copper) and Tongling Jinxin.

The 29 sites total about **11.7 Mt/yr**, roughly 47 % of world smelting
capacity.

## Evidence grades

| Grade | Meaning | Sites |
|---|---|---|
| A | dated 2025-26 operator document confirms activity **and** site capacity | 8 |
| B | dated operator document confirms activity; capacity from a secondary source | 11 |
| C | no dated operator confirmation; checked by satellite instead | 10 |

Grade-C sites were checked with VIIRS detections and Sentinel-2 hot pixels:
8 are active, 2 (Onsan, Toyo) are likely active. Chuquicamata showed almost no
heat in June-July 2026.

## Exclusions

Altonorte, Ventanas, Paipote, Potrerillos, Guangxi Nanguo, Daye Yangxin
Hongsheng, Sterlite Tuticorin and Mount Isa are excluded (closed, suspended or
unconfirmed). Reasons and sources are in `smelter_excluded.csv`.

## Geolocation

No web gazetteer was reachable from the build environment, so coordinates were
derived from the satellite data (`orbitcu.geolocate`):

1. start from a town or address-level prior;
2. find persistent VIIRS heat clusters within 25 km (one year of detections,
   1.5 km clustering);
3. confirm with Sentinel-2 hot pixels (thermal anomaly index > 0.45);
4. assign a confidence grade.

| Confidence | Sites | Action |
|---|---|---|
| high | 10 | ready |
| medium | 5 | quick map check advised |
| low | 14 | `needs_review = True`, must be placed by hand before the backfill |

Low-confidence sites: Jinguan and Jinlong (Tongling), Chifeng Yunjin and
Chifeng Jintong, Daye, Heding, Dongying, Southeast, Dahej, Gresik, Manyar,
Toyo, Onahama and Kamoa-Kakula. A heat cluster is not proof of a copper
smelter: steel works, refineries and power plants are also persistent heat
sources.
