"""Interactive smelter map built from the project's computed results.

One self-contained HTML page (base map, d3 and topojson-client embedded; no network at view time)
that shows, for each registry smelter:

* its current position (and the previous one when the team moved it);
* the weekly activity status from VIIRS night heat and the dated stop / restart
  events (``activity_index``);
* the fast VIIRS-only stop alerts;
* quarterly copper output estimated from heat against the published figures
  (``production``);
* the surprise alerts (VIIRS flag, else the share of hot Sentinel-2/Landsat images);
* the monthly image heat score (share of clear images with a confirmed hot spot);
* the annual SO2 emission estimate where the plume is visible (version-corrected);

plus two world panels: the capacity-weighted running share and the China index
against the official NBS refined copper statistic.

Nothing is recomputed here: the page reads the result files, so after any
recomputation (new positions, new months) rebuild it with::

    orbitcu sitemap --data <project data dir> --out figures/carte_statut_fonderies.html

``--data`` is the folder that holds ``activity_index/``, ``production/``,
``image_scores/``, ``so2/`` and ``trading/`` (the project's shared data folder).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .sites import REGISTRY_DIR, SITES_CSV

TEMPLATES = Path(__file__).resolve().parent / "templates"
VENDOR = {"https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js": "d3.min.js",
          "https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js": "topojson-client.min.js"}

# Published figures and surprise cases are kept per reporting entity; a few
# entities cover several registry sites.
ENTITY_SITE = {"PL-KGHM": "PL-GLOGOW"}
ENTITY_NOTE = {"PL-GLOGOW": "Chiffres publiés KGHM : Glogow + Legnica (cuivre électrolytique). "
                            "L'estimation affichée additionne les deux fonderies."}
# Sites whose SO2 plume is seen every year (see the SO2 section of the report).
SO2_SITES = ["RU-NORILSK-CU", "IR-SARCHESHMEH", "CN-JINCHANG"]
METRIC_FR = {
    "cathode_cu": "cuivre cathode", "anode_cu": "cuivre anode", "concentrate": "concentré traité",
    "cu_metal_ex_transbaikal": "cuivre métal (hors Transbaïkal)", "cu_sales": "ventes de cuivre",
    "cathode_cu_group_proxy": "cathode du groupe (approximation)", "cu_country_proxy": "pays (approximation)",
}


def _metric(x) -> str:
    if pd.isna(x.metric):  # KGHM monthly sheet: electrolytic (cathode) copper
        return METRIC_FR["cathode_cu"] if x.site_id == "PL-KGHM" else "non précisé"
    return METRIC_FR.get(x.metric, str(x.metric))


def _r(x, nd=3):
    if x is None or (isinstance(x, float) and not np.isfinite(x)) or pd.isna(x):
        return None
    return round(float(x), nd)


def _read(path: Path, **kw) -> pd.DataFrame:
    return pd.read_csv(path, **kw) if path.exists() else pd.DataFrame()


def surprise_alerts(prod: Path) -> pd.DataFrame:
    """Recommended surprise detector: the VIIRS flag when VIIRS flags, else the
    hot-image share flag (both thresholds fixed in advance, point in time)."""
    a = _read(prod / "images" / "surprise_r.csv")
    b = _read(prod / "images" / "surprise_r_img.csv")
    if a.empty:
        return a
    if not b.empty:
        a = a.merge(b[["entity", "period_start", "flag", "r_img"]], on=["entity", "period_start"],
                    how="left", suffixes=("", "_img"))
        a["flag_img"] = a["flag_img"].fillna(0)
        a["source"] = np.where(a["flag"] != 0, "VIIRS", np.where(a["flag_img"] != 0, "images", ""))
        a["flag"] = np.where(a["flag"] != 0, a["flag"], a["flag_img"])
    else:
        a["source"] = np.where(a["flag"] != 0, "VIIRS", "")
        a["r_img"] = np.nan
    return a


def build_data(data_dir: Path, sites_csv: Path = SITES_CSV) -> dict:
    ai, prod, img, so2 = (data_dir / d for d in ("activity_index", "production", "image_scores", "so2"))
    sites = pd.read_csv(sites_csv)
    moves = _read(REGISTRY_DIR / "position_changes.csv")
    moves = {r.site_id: r for r in moves.itertuples()} if not moves.empty else {}

    # weekly status: one shared week axis, per site a status string + ratio list
    ws = _read(ai / "weekly_status.csv")
    weeks = sorted(ws["week_end"].unique()) if not ws.empty else []
    wpos = {w: i for i, w in enumerate(weeks)}
    code = {"running": "r", "stopped": "s", "unknown": "u"}
    ev = _read(ai / "events.csv")
    fa = _read(data_dir / "trading" / "fast_alerts.csv")
    est = _read(prod / "estimates_quarterly.csv")
    rep = _read(prod / "reported_quarterly.csv")
    sur = surprise_alerts(prod)
    im = _read(img / "image_scores_monthly.csv")
    cov = _read(img / "site_coverage.csv")
    so2a = _read(so2 / "version_correction" / "so2_annual_corrected.csv")
    if so2a.empty:
        so2a = _read(so2 / "results_2022_2026" / "so2_emissions_annual.csv")

    def for_site(df, sid, col="site_id"):
        if df.empty:
            return df
        keys = [sid] + [e for e, s in ENTITY_SITE.items() if s == sid]
        return df[df[col].isin(keys)]

    out = []
    for s in sites.itertuples(index=False):
        sid = s.id
        st = ["u"] * len(weeks)
        rr = [None] * len(weeks)
        for w in for_site(ws, sid).itertuples(index=False):
            i = wpos[w.week_end]
            st[i] = code.get(w.status, "u")
            rr[i] = _r(w.r, 2)
        events = [{"event": e.event, "start": e.est_start, "seen": e.seen_date, "end": e.end_week,
                   "weeks": int(e.weeks), "min_r": _r(e.min_r, 2)}
                  for e in for_site(ev, sid).itertuples(index=False)]
        alerts = [{"gap_start": a.gap_start, "alert": a.alert_date,
                   "restart": None if pd.isna(a.restart) else a.restart,
                   "gap_days": None if pd.isna(a.gap_days) else int(a.gap_days)}
                  for a in for_site(fa, sid).itertuples(index=False)]
        # production: estimate (KGHM: Glogow + Legnica), reported figures
        e = est[est["site_id"].isin(["PL-GLOGOW", "PL-LEGNICA"] if sid == "PL-GLOGOW" else [sid])] if not est.empty else est
        estq = []
        if not e.empty:
            g = e.groupby("quarter").agg(est_kt=("est_kt", "sum"), normal_kt=("normal_kt", "sum"),
                                         r=("r", "mean"), basis=("rate_basis", "first"),
                                         visible=("visible", "all")).reset_index()
            estq = [{"q": q.quarter, "est": _r(q.est_kt, 1), "normal": _r(q.normal_kt, 1), "r": _r(q.r, 2),
                     "basis": q.basis, "visible": bool(q.visible)} for q in g.itertuples(index=False)]
        reported = [{"start": x.period_start, "end": x.period_end, "kt": _r(x.cu_kt, 1),
                     "metric": _metric(x), "published": str(x.published_at)[:10],
                     "source": None if pd.isna(x.source) else x.source} for x in for_site(rep, sid).itertuples(index=False)]
        surprises = []
        for x in for_site(sur, sid, "entity").itertuples(index=False):
            if x.flag == 0 and x.surprise == 0:
                continue
            surprises.append({"start": x.period_start, "end": x.period_end, "kt": _r(x.cu_kt, 1),
                              "naive": _r(x.naive_kt, 1), "surprise": int(x.surprise), "flag": int(x.flag),
                              "source": x.source, "lead": None if pd.isna(x.lead_days) else int(x.lead_days),
                              "decision": str(x.decision_time)[:10], "published": str(x.published_at)[:10]})
        images = [{"m": x.month, "share": _r(x.hot_share, 2), "n": int(x.n_usable)}
                  for x in for_site(im, sid).itertuples(index=False) if x.n_usable > 0]
        c = for_site(cov, sid)
        so2_rows = []
        if sid in SO2_SITES and not so2a.empty:
            so2_rows = [{"y": str(x.period), "e": _r(x.E_kt_yr, 1), "se": _r(x.E_se_kt_yr, 1)}
                        for x in so2a[so2a["site_id"] == sid].itertuples(index=False)]
        mv = moves.get(sid)
        out.append({
            "id": sid, "name": s.smelter, "country": s.country, "operator": s.operator,
            "capacity_kt": None if pd.isna(s.capacity_kt_cu) else float(s.capacity_kt_cu),
            "grade": s.operator_check, "confidence": s.geoloc_confidence,
            "lat": float(s.lat), "lon": float(s.lon),
            "moved": None if mv is None else {"on": mv.changed_on, "lat": float(mv.old_lat), "lon": float(mv.old_lon)},
            "status": "".join(st), "r": rr, "events": events, "alerts": alerts,
            "est": estq, "reported": reported, "surprises": surprises, "entity_note": ENTITY_NOTE.get(sid),
            "images": images,
            "img_cov": None if c.empty else {"n": int(c.iloc[0]["n"]), "clear": _r(c.iloc[0]["clear"], 3),
                                              "per_month": _r(c.iloc[0]["usable_per_month"], 1)},
            "so2": so2_rows,
        })

    wi = _read(ai / "weekly_index.csv")
    world = [{"w": x.week_end, "share": _r(x.running_share, 3), "cov": _r(x.coverage, 3)}
             for x in wi.itertuples(index=False)] if not wi.empty else []
    cn = _read(ai / "china_index_vs_nbs.csv")
    china = [{"m": x.month, "sat": _r(x.sat_running_share, 1), "nbs": _r(x.nbs_dev_pct, 1)}
             for x in cn.itertuples(index=False)] if not cn.empty else []
    if not sur.empty:
        fl = sur[sur["flag"] != 0]
        hit = (np.sign(fl["flag"]) == np.sign(fl["surprise"])) & (fl["surprise"] != 0)
        sur_score = {"flags": int(len(fl)), "hits": int(hit.sum()), "surprises": int((sur["surprise"] != 0).sum()),
                     "lead": None if not hit.any() else int(fl.loc[hit, "lead_days"].median())}
    else:
        sur_score = None
    return {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "weeks": weeks,
            "sites": out, "world": world, "china": china, "surprise_score": sur_score}


def render(data: dict, out: Path, template: Path = TEMPLATES / "site_map.html") -> Path:
    html = template.read_text(encoding="utf-8")
    world = (TEMPLATES / "countries-50m.json").read_text(encoding="utf-8")
    html = html.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    html = html.replace("/*__WORLD__*/null", world)
    # inline the two libraries so the page opens without network
    for url, name in VENDOR.items():
        lib = TEMPLATES / "vendor" / name
        if lib.exists():
            html = html.replace(f'<script src="{url}"></script>', "<script>" + lib.read_text(encoding="utf-8") + "</script>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="orbitcu sitemap",
                                description="Build the interactive smelter map from the computed results (HTML).")
    p.add_argument("--data", type=Path, required=True,
                   help="folder with activity_index/, production/, image_scores/, so2/, trading/")
    p.add_argument("--out", type=Path, default=Path("output/carte_statut_fonderies.html"))
    p.add_argument("--json", type=Path, default=None, help="also write the data as JSON")
    p.add_argument("--open", action="store_true", help="open the page in the web browser")
    a = p.parse_args(argv)
    data = build_data(a.data)
    if a.json:
        a.json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    out = render(data, a.out).resolve()
    print(f"Smelter map ({len(data['sites'])} sites, {len(data['weeks'])} weeks) -> {out}")
    if a.open:
        import webbrowser
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
