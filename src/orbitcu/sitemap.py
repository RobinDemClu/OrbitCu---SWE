"""Interactive smelter map built from the project's computed results.

One self-contained HTML page (base map, fonts, d3 and topojson-client embedded; no network at view time)
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
import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .sites import REGISTRY_DIR, SITES_CSV

TEMPLATES = Path(__file__).resolve().parent / "templates"
VENDOR = {"https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js": "d3.min.js",
          "https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js": "topojson-client.min.js"}
FONT_FAMILIES = {"saira-condensed": "Saira Condensed", "ibm-plex-sans": "IBM Plex Sans", "ibm-plex-mono": "IBM Plex Mono"}

# Published figures and surprise cases are kept per reporting entity; a few
# entities cover several registry sites.
ENTITY_SITE = {"PL-KGHM": "PL-GLOGOW"}
ENTITY_NOTE = {"PL-GLOGOW": "KGHM publishes Glogow + Legnica together (electrolytic copper); "
                            "the estimate shown adds the two smelters."}
# Sites whose SO2 plume is seen every year (see the SO2 section of the report).
SO2_SITES = ["RU-NORILSK-CU", "IR-SARCHESHMEH", "CN-JINCHANG"]
METRIC_LABEL = {
    "cathode_cu": "cathode copper", "anode_cu": "anode copper", "concentrate": "concentrate smelted",
    "cu_metal_ex_transbaikal": "copper metal (excl. Transbaikal)", "cu_sales": "copper sales",
    "cathode_cu_group_proxy": "group cathode (proxy)", "cu_country_proxy": "country total (proxy)",
}


def _metric(x) -> str:
    if pd.isna(x.metric):  # KGHM monthly sheet: electrolytic (cathode) copper
        return METRIC_LABEL["cathode_cu"] if x.site_id == "PL-KGHM" else "not stated"
    return METRIC_LABEL.get(x.metric, str(x.metric))


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
    if not ws.empty:  # the first year has no seasonal reference: start at the first week with a known running site
        first = ws.loc[ws["status"] == "running", "week_end"].min()
        ws = ws[ws["week_end"] >= first]
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
    if not wi.empty and weeks:
        wi = wi[wi["week_end"].isin(set(weeks))]
    world = [{"w": x.week_end, "share": _r(x.running_share, 3), "cov": _r(x.coverage, 3),
              "run": _r(x.running_kt, 0), "known": _r(x.known_kt, 0), "total": _r(x.total_kt, 0)}
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
    _add_profile(out, data_dir)
    return {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "weeks": weeks,
            "sites": out, "world": world, "china": china, "surprise_score": sur_score,
            "production": _production_totals(prod, sites), "markets": _markets(data_dir)}


# Listed companies linked to each smelter (operator or main shareholder), Yahoo tickers.
SITE_TICKERS = {
    "CN-GUIXI": ["600362.SS"], "KR-ONSAN": ["006260.KS"], "CD-KAMOA": ["IVN.TO", "2899.HK"],
    "IN-KUTCH": ["ADANIENT.NS"], "IN-DAHEJ": ["HINDALCO.NS"], "CN-JINGUAN": ["000630.SZ"],
    "PL-GLOGOW": ["KGH.WA"], "DE-HAMBURG": ["NDA.DE"], "BG-PIRDOP": ["NDA.DE"], "JP-TOYO": ["5713.T"],
    "JP-SAGANOSEKI": ["5016.T"], "CN-CHIFENG-YJ": ["000878.SZ"], "CN-SOUTHEAST": ["000878.SZ"],
    "PE-ILO": ["SCCO"], "CN-HEDING": ["600362.SS"], "CN-JINLONG": ["000630.SZ"], "ID-GRESIK": ["FCX", "5711.T"],
    "CN-DAYE": ["0661.HK"], "CN-CHIFENG-JT": ["000630.SZ"], "JP-ONAHAMA": ["5711.T"], "ID-MANYAR": ["FCX"],
}
TICKER_NAMES = {
    "600362.SS": "Jiangxi Copper", "006260.KS": "LS Corp", "IVN.TO": "Ivanhoe Mines", "2899.HK": "Zijin Mining",
    "ADANIENT.NS": "Adani Enterprises", "HINDALCO.NS": "Hindalco", "000630.SZ": "Tongling Nonferrous",
    "KGH.WA": "KGHM", "NDA.DE": "Aurubis", "5713.T": "Sumitomo Metal Mining", "5016.T": "JX Advanced Metals",
    "000878.SZ": "Yunnan Copper", "SCCO": "Southern Copper", "FCX": "Freeport-McMoRan", "5711.T": "Mitsubishi Materials",
    "0661.HK": "China Daye Non-Ferrous", "GLEN.L": "Glencore",
}
TICKER_CCY = {"600362.SS": "CNY", "006260.KS": "KRW", "IVN.TO": "CAD", "2899.HK": "HKD", "ADANIENT.NS": "INR",
              "HINDALCO.NS": "INR", "000630.SZ": "CNY", "KGH.WA": "PLN", "NDA.DE": "EUR", "5713.T": "JPY",
              "5016.T": "JPY", "000878.SZ": "CNY", "SCCO": "USD", "FCX": "USD", "5711.T": "JPY", "0661.HK": "HKD",
              "GLEN.L": "GBp"}
FABRICATORS = {"Prysmian": "Prysmian (cables)", "Nexans": "Nexans (cables)", "Mueller": "Mueller Industries (tubes)",
               "Furukawa": "Furukawa Electric", "SumitomoElec": "Sumitomo Electric", "Fujikura": "Fujikura",
               "Hailiang": "Hailiang (tubes)"}


def _thumb(path: Path) -> str | None:
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii") if path.exists() else None


def _add_profile(out: list[dict], data_dir: Path) -> None:
    """Identity-card facts: operator evidence from the registry, linked tickers,
    satellite thumbnails, yearly output estimates, latest SO2."""
    reg = _read(REGISTRY_DIR / "smelter_registry.csv")
    reg = {r["id"]: r for r in reg.to_dict("records")} if not reg.empty else {}
    sites = _read(SITES_CSV)
    geo = {r["id"]: r for r in sites.to_dict("records")} if not sites.empty else {}
    th_dir = REGISTRY_DIR / "thumbnails"
    th = _read(th_dir / "thumbnails.csv")
    th = {r["site_id"]: r for r in th.to_dict("records")} if not th.empty else {}
    yr = _read(data_dir / "production" / "estimates_by_site_year.csv")
    yr = {r["site_id"]: r for r in yr.to_dict("records")} if not yr.empty else {}
    total_cap = sum(x["capacity_kt"] or 0 for x in out)

    def clean(v):
        return None if v is None or (isinstance(v, float) and np.isnan(v)) else v

    for x in out:
        r, g = reg.get(x["id"], {}), geo.get(x["id"], {})
        x["profile"] = {
            "rank": int(g["rank"]) if g.get("rank") is not None and not pd.isna(g.get("rank")) else None,
            "reg_share": None if not x["capacity_kt"] else round(x["capacity_kt"] / total_cap * 100, 1),
            "capacity_basis": clean(r.get("capacity_basis")), "capacity_source": clean(r.get("capacity_source")),
            "evidence": clean(r.get("operator_activity_evidence")), "evidence_source": clean(r.get("operator_activity_source")),
            "evidence_date": clean(r.get("operator_activity_date")),
            "capacity_evidence": clean(r.get("operator_capacity_evidence")),
            "geo_method": clean(g.get("geoloc_method")), "geo_note": clean(g.get("geoloc_note")),
            "tickers": [{"t": t, "name": TICKER_NAMES.get(t, t), "ccy": TICKER_CCY.get(t)} for t in SITE_TICKERS.get(x["id"], [])],
        }
        t = th.get(x["id"])
        x["thumb"] = None if t is None else {"rgb": _thumb(th_dir / f"{x['id']}_rgb.jpg"), "swir": _thumb(th_dir / f"{x['id']}_swir.jpg"),
                                             "date": t["acquired"], "hot": bool(t["hot_seen"]), "half_km": float(t["half_km"])}
        y = yr.get(x["id"])
        x["est_year"] = None if y is None else {k: _r(v, 0) for k, v in y.items() if k[:4].isdigit()}
        x["so2_last"] = x["so2"][-1] if x["so2"] else None


def _production_totals(prod: Path, sites: pd.DataFrame) -> dict:
    est = _read(prod / "estimates_quarterly.csv")
    if est.empty:
        return {}
    q = est.groupby(["quarter", "country"])["est_kt"].sum().unstack(fill_value=0).sort_index()
    order = q.sum().sort_values(ascending=False).index.tolist()
    return {"quarters": q.index.tolist(), "countries": order,
            "values": {c: [round(float(v), 0) for v in q[c]] for c in order}}


def _markets(data_dir: Path) -> dict:
    t = data_dir / "trading"
    out = {}
    hg, lme = _read(t / "comex_hg_weekly.csv"), _read(t / "lme_cu_3m_weekly.csv")
    if not hg.empty and not lme.empty:
        m = hg.merge(lme, on="week_monday", how="outer").sort_values("week_monday")
        sp = _read(t / "lme_cu_spread_stocks_weekly.csv")
        if not sp.empty:
            m = m.merge(sp, on="week_monday", how="left")
        az = _read(t / "lme_al_zn_3m_weekly.csv")
        if not az.empty:
            m = m.merge(az, on="week_monday", how="left")
        out["weekly"] = {"w": m["week_monday"].tolist(),
                         **{c: [_r(v, 4) for v in m[c]] for c in m.columns if c != "week_monday"}}
    sh = _read(t / "operator_shares_monthly.csv")
    if not sh.empty:
        out["shares"] = {"w": sh["week_monday"].tolist(),
                         "series": [{"t": c, "name": TICKER_NAMES.get(c, c), "ccy": TICKER_CCY.get(c),
                                     "sites": [k for k, v in SITE_TICKERS.items() if c in v],
                                     "v": [_r(v, 5) for v in sh[c]]} for c in sh.columns if c != "week_monday"]}
    fb = _read(t / "fabricators_weekly_log.csv")
    if not fb.empty:
        out["fabricators"] = {"w": fb["week_monday"].tolist(),
                              "series": [{"t": k, "name": n, "v": [_r(100 * np.exp(v), 2) for v in fb[f"{k}_stock"]]}
                                         for k, n in FABRICATORS.items() if f"{k}_stock" in fb]}
    return out


def render(data: dict, out: Path, template: Path = TEMPLATES / "site_map.html") -> Path:
    html = template.read_text(encoding="utf-8")
    world = (TEMPLATES / "countries-50m.json").read_text(encoding="utf-8")
    html = html.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    html = html.replace("/*__WORLD__*/null", world)
    # embed the fonts (SIL OFL, from Fontsource) instead of loading Google Fonts
    fonts = TEMPLATES / "vendor" / "fonts"
    faces = []
    for f in sorted(fonts.glob("*.woff2")):
        family, weight = FONT_FAMILIES[f.name.split("-latin-")[0]], f.name.split("-latin-")[1].split("-")[0]
        b64 = base64.b64encode(f.read_bytes()).decode("ascii")
        faces.append(f'@font-face{{font-family:"{family}";font-style:normal;font-weight:{weight};font-display:swap;'
                     f'src:url(data:font/woff2;base64,{b64}) format("woff2")}}')
    if faces:
        html = re.sub(r'<link rel="preconnect" href="https://fonts.googleapis.com">\s*<link rel="stylesheet" href="https://fonts.googleapis.com[^"]*">',
                      lambda _: "<style>" + "".join(faces) + "</style>", html)
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
