# FR-2 prototype results (2025-11-01 to 2026-10-31)

Three sites, one year, run from the cloud build environment on 2026-11-04.
"Clear" = AOI cloud fraction < 10 % and no-data < 5 %. "Hot scene" = at least
3 Sentinel-2 pixels with TAI > 0.45 inside the 3 x 3 km thermal box.

| Site | Geoloc. | S2 scenes (clear) | S2 hot scenes | Landsat 8/9 scenes (clear) | VIIRS detections (days) |
|---|---|---|---|---|---|
| Glogow (PL-GLOGOW) | high | 203 (62) | 59 / 62 | 44 (10) | 2001 (276 days) |
| Hamburg (DE-HAMBURG) | medium | 100 (27) | 21 / 27 | 71 (21) | 580 (169 days) |
| Kamoa-Kakula (CD-KAMOA) | low | 102 (53) | 8 / 53 | 56 (34) | 61 (19 days) |

No request failed after retries. Total stored: 89 MB (clips + manifests).

Point-in-time lag (`available_at - acquired_at`, median): Sentinel-2 4-5 h
(`s2:generation_time`), Landsat 19-32 days (STAC `created`; Collection-2 L2 is
produced days to weeks after acquisition), VIIRS 3 h (NRT rule).

Reading:

* **Glogow** is the reference case: the smelter is hot on 95 % of clear
  Sentinel-2 scenes and VIIRS sees it on 276 of 365 days.
* **Hamburg** is clearly active at the chosen point (78 % of clear scenes hot);
  the point is 2.4 km from the street-address prior, so it should be confirmed
  on a map before relying on it.
* **Kamoa-Kakula** shows very little heat in the box. The smelter only started
  in late 2025 and the location is still a prior; the box probably misses the
  furnace building. This site must be located by hand before the backfill.

## Grade-C activity check

`data/registry/c_site_activity_check.csv`: distinct VIIRS detection days per
month within 1.5 km (S-NPP; SP to July 2026, NRT from August 2026) plus
Sentinel-2 hot-pixel persistence on 10 clear 2026 scenes.

| Site | Geoloc. | Verdict | VIIRS days, Nov 2025 - Oct 2026 |
|---|---|---|---|
| Sarcheshmeh | high | active | 294 |
| Chuquicamata | high | active (near-zero June-July 2026) | 237 |
| Xiangguang | medium | active | 235 |
| Norilsk Copper Plant | high | active | 206 |
| Jinchuan Fangchenggang | medium | active | 183 |
| Dongying Fangyuan | low | active | 173 |
| Chifeng Jintong | low | active | 159 |
| Jinchuan HQ (Jinchang) | high | active | 158 |
| Onsan | medium | likely active (S2 hot pixels on 10/10 scenes; VIIRS weak) | 48 |
| Toyo | low | likely active (S2 hot pixels on 8/10 scenes; no VIIRS) | 0 |

"Active" at a low-confidence location means an industrial heat source at that
point is running; it proves the smelter is running only once the location is
confirmed.
