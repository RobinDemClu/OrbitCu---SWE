"""SO2 emission rate of each smelter from Sentinel-5P columns and ERA5 winds.

Method (Fioletov et al., 2015, 2016, 2017, 2020)
-------------------------------------------------
For a point source at the origin and a wind of speed ``s`` blowing along +y,
the time-averaged SO2 vertical column at (x, y) is modelled as

    V(x, y) = a * f(x, y) * g(y, s) + B

* ``f(x) = exp(-x^2 / (2 sigma^2)) / (sqrt(2 pi) sigma)`` - cross-wind
  Gaussian with a constant width, as in Fioletov et al. (2017);
* ``g(y, s) = (lam/2) exp(lam (lam sigma^2 - 2 y)/2) erfc((lam sigma^2 - y)/(sqrt 2 sigma))``
  - an exponentially-modified Gaussian along the wind: the plume decays
  with lifetime ``tau`` so ``lam = 1 / (s tau)`` (units 1/km);
* ``a`` is the total SO2 mass around the source (mol) and ``B`` a background,
  planar by default (``B0 + Bx x + By y``, x/y east/north) to absorb regional
  gradients (coast lines, terrain, distant sources), as in the multi-source
  fits of Fioletov et al. (2017).

The emission rate is ``E = a / tau``. With ``sigma`` and ``tau`` fixed - as in
Fioletov et al. (2016, 2017) and the TROPOMI catalogue of Fioletov et al.
(2020) - the model is **linear in a and B**, so each pixel can carry its own
wind and a window of pixels (one month, one quarter, one year) is fitted by
ordinary least squares. Each pixel is rotated into its own wind frame (the
ERA5 100 m wind at the source, linearly interpolated to the pixel time;
point series from the CDS "ERA5 time series" dataset).

Defaults: ``tau = 6 h`` (the lifetime Fioletov et al. use for all sources in
their OMI/TROPOMI catalogues), ``sigma = 10 km`` (TROPOMI pixel 5.5 x 3.5 km
plus plume width; Fioletov et al. 2020 use 10-15 km for TROPOMI), fit radius
``40 km``. Column: the 1 km box-profile SO2 column (boundary layer), QA >= 0.5.
Winds below 0.5 m/s are dropped (rotation undefined).

Uncertainty: the OLS standard error of ``a`` (inflated by the autocorrelation
of pixels from the same orbit via a per-orbit cluster bootstrap) and the
systematic factor on ``tau``/``sigma``/AMF, which Fioletov et al. put at
about +/-50 % for individual sources. That systematic part cancels in the
copper conversion, which is calibrated against reported output.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import erfc

M_SO2 = 64.066e-3  # kg/mol
SECONDS_PER_YEAR = 365.25 * 86400


@dataclass(frozen=True)
class FitConfig:
    tau_h: float = 6.0
    sigma_km: float = 10.0
    radius_km: float = 40.0
    min_wind: float = 0.5
    column: str = "so2_1km"
    qa_min: float = 0.5
    min_pixels: int = 150
    min_orbits: int = 5
    gradient: bool = True


def local_xy_km(lat, lon, lat0, lon0):
    """Equirectangular east/north offsets in km (fine at <= 60 km)."""
    x = (np.asarray(lon) - lon0 + 180) % 360 - 180
    x = np.radians(x) * 6371.0 * np.cos(np.radians(lat0))
    y = np.radians(np.asarray(lat) - lat0) * 6371.0
    return x, y


def rotate(x, y, u, v):
    """Rotate (x, y) so that the wind (u, v) points along +y.

    Returns (xr, yr): cross-wind and along-wind coordinates (km); yr > 0 is
    downwind of the source.
    """
    s = np.hypot(u, v)
    ux, uy = u / s, v / s
    yr = x * ux + y * uy
    xr = x * uy - y * ux
    return xr, yr


def plume_kernel(xr, yr, wind_ms, cfg: FitConfig):
    """f(x) * g(y, s) in km^-2 (multiply by a [mol] to get mol km^-2)."""
    sig = cfg.sigma_km
    lam = 1.0 / (wind_ms * 3.6 * cfg.tau_h)  # 1/km  (m/s * 3.6 = km/h)
    f = np.exp(-xr ** 2 / (2 * sig ** 2)) / (np.sqrt(2 * np.pi) * sig)
    arg = (lam * sig ** 2 - yr) / (np.sqrt(2) * sig)
    # log-space product to avoid overflow of exp() * erfc() far upwind/downwind
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        g = 0.5 * lam * np.exp(lam * (lam * sig ** 2 - 2 * yr) / 2 + np.log(np.maximum(erfc(arg), 1e-300)))
    g = np.where(np.isfinite(g), g, 0.0)
    return f * g


# --------------------------------------------------------------------- winds
def load_winds(era5_ts_dir: Path, site_id: str) -> pd.DataFrame | None:
    """Hourly ERA5 point series of one site (see ``era5.retrieve_timeseries``)."""
    files = sorted(Path(era5_ts_dir).glob(f"{site_id}_*.parquet"))
    if not files:
        return None
    w = pd.concat([pd.read_parquet(f) for f in files]).drop_duplicates("valid_time")
    return w.sort_values("valid_time").reset_index(drop=True)


def _epoch_s(t: pd.Series) -> np.ndarray:
    t = pd.to_datetime(t, utc=True)
    return (t - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy()


def winds_at_pixels(w: pd.DataFrame, px: pd.DataFrame, level: str = "100") -> pd.DataFrame:
    """ERA5 wind at the source, linearly interpolated to each pixel's time.

    Fioletov et al. rotate every pixel with the wind at the *source* at
    overpass time; within the 40 km fit radius the spatial variation of the
    reanalysis wind is small compared with its hourly variation.
    """
    # convert explicitly to seconds: parquet files may store ms or ns units
    t = _epoch_s(w["valid_time"])
    tp = _epoch_s(px["pixel_time_utc"])
    u = np.interp(tp, t, w[f"u{level}"].values, left=np.nan, right=np.nan)
    v = np.interp(tp, t, w[f"v{level}"].values, left=np.nan, right=np.nan)
    return pd.DataFrame({"u": u, "v": v}, index=px.index)


# ----------------------------------------------------------------------- fit
def prepare(px: pd.DataFrame, site, ds, cfg: FitConfig) -> pd.DataFrame:
    """Add wind-frame coordinates and the plume kernel to the pixels of one site."""
    px = px[(px["qa_value"] >= cfg.qa_min) & px[cfg.column].notna()].copy()
    x, y = local_xy_km(px["lat"].values, px["lon"].values, site.lat, site.lon)
    px["x_km"], px["y_km"] = x, y
    px = px[np.hypot(x, y) <= cfg.radius_km]
    if px.empty or ds is None:
        return px.iloc[0:0]
    w = winds_at_pixels(ds, px)
    px[["u", "v"]] = w
    px["wind"] = np.hypot(px["u"], px["v"])
    px = px[px["wind"] >= cfg.min_wind]
    xr_, yr_ = rotate(px["x_km"].values, px["y_km"].values, px["u"].values, px["v"].values)
    px["xr_km"], px["yr_km"] = xr_, yr_
    px["kernel"] = plume_kernel(xr_, yr_, px["wind"].values, cfg)
    px["vcd_mol_km2"] = px[cfg.column] * 1e6  # mol m-2 -> mol km-2
    return px


def fit_window(px: pd.DataFrame, cfg: FitConfig, n_boot: int = 200, seed: int = 0) -> dict:
    """OLS fit of V = a*K + B on prepared pixels; emission in kt SO2 / year."""
    n, n_orb = len(px), px["item_id"].nunique() if len(px) else 0
    res = {"n_pixels": n, "n_orbits": n_orb, "a_mol": np.nan, "B_mol_km2": np.nan,
           "E_kt_yr": np.nan, "E_se_kt_yr": np.nan, "t_stat": np.nan, "mean_wind": np.nan}
    if n < cfg.min_pixels or n_orb < cfg.min_orbits:
        return res
    cols = [px["kernel"].values, np.ones(n)]
    if cfg.gradient:
        # planar background (regional gradients: terrain, coast, distant sources)
        cols += [px["x_km"].values / cfg.radius_km, px["y_km"].values / cfg.radius_km]
    if "kernel2" in px:
        cols.append(px["kernel2"].values)  # neighbouring source, fitted jointly
    X = np.column_stack(cols)
    yv = px["vcd_mol_km2"].values
    coef, *_ = np.linalg.lstsq(X, yv, rcond=None)
    # cluster (per orbit) bootstrap for the standard error of a
    rng = np.random.default_rng(seed)
    groups = px.groupby("item_id").indices
    keys = list(groups)
    boots = []
    for _ in range(n_boot):
        pick = rng.choice(len(keys), len(keys), replace=True)
        idx = np.concatenate([groups[keys[k]] for k in pick])
        c, *_ = np.linalg.lstsq(X[idx], yv[idx], rcond=None)
        boots.append(c[0])
    se_a = float(np.std(boots, ddof=1))
    to_kt_yr = M_SO2 / (cfg.tau_h * 3600) * SECONDS_PER_YEAR / 1e6  # mol -> kt/yr
    if "kernel2" in px:
        res["E2_kt_yr"] = coef[-1] * to_kt_yr
    res.update(a_mol=coef[0], B_mol_km2=coef[1], E_kt_yr=coef[0] * to_kt_yr,
               E_se_kt_yr=se_a * to_kt_yr, t_stat=coef[0] / se_a if se_a > 0 else np.nan,
               mean_wind=float(px["wind"].mean()))
    return res


def add_second_source(prep: pd.DataFrame, dx: float, dy: float, cfg: FitConfig) -> pd.DataFrame:
    prep = prep.copy()
    xr_, yr_ = rotate(prep["x_km"].values - dx, prep["y_km"].values - dy, prep["u"].values, prep["v"].values)
    prep["kernel2"] = plume_kernel(xr_, yr_, prep["wind"].values, cfg)
    return prep


def neighbour_sources(pixels: pd.DataFrame, sites, era5_ts_dir: Path, cfg: FitConfig = FitConfig(),
                      locate_months: int = 12, min_offset_km: float = 7.5) -> pd.DataFrame:
    """Locate, per site, the dominant SO2 source from its first ``locate_months`` months.

    Only the first months are used so that the choice of model for later
    periods does not depend on later data (point-in-time). A source found
    more than ``min_offset_km`` from the furnace is kept as a second,
    jointly-fitted source.
    """
    rows = []
    for site in sites:
        sp = pixels[pixels["site_id"] == site.id]
        w = load_winds(era5_ts_dir, site.id)
        prep = prepare(sp, site, w, cfg) if not sp.empty else sp
        if prep.empty:
            continue
        m = prep["pixel_time_utc"].dt.tz_convert(None).dt.to_period("M")
        first = prep[m < m.min() + locate_months]
        loc = locate_source(first, cfg)
        # significance of the source at the best position (cluster bootstrap)
        moved = first.copy()
        xr_, yr_ = rotate(first["x_km"].values - loc["dx_km"], first["y_km"].values - loc["dy_km"],
                          first["u"].values, first["v"].values)
        moved["kernel"] = plume_kernel(xr_, yr_, first["wind"].values, cfg)
        t_best = fit_window(moved, cfg, n_boot=100)["t_stat"]
        loc["t_best"] = t_best
        keep = bool(loc["offset_km"] > min_offset_km and t_best > 4)
        loc.update(site_id=site.id, second_source=keep,
                   located_on=f"{m.min()}..{m.min() + locate_months - 1}")
        rows.append(loc)
    return pd.DataFrame(rows)


def monthly_emissions(pixels: pd.DataFrame, sites, era5_ts_dir: Path, cfg: FitConfig = FitConfig(),
                      freq: str = "M", neighbours: pd.DataFrame | None = None) -> pd.DataFrame:
    """Emission per (site, period). ``available_at`` = latest input availability.

    The ERA5 availability (valid time + 5 days) and the S5P processing time are
    both respected: a period's estimate becomes knowable at the max of the two
    over the pixels used.
    """
    from .ingest.pit import LATENCY

    rows = []
    for site in sites:
        sp = pixels[pixels["site_id"] == site.id]
        if sp.empty:
            continue
        ds = load_winds(era5_ts_dir, site.id)
        prep = prepare(sp, site, ds, cfg)
        if prep.empty:
            continue
        if neighbours is not None and not neighbours.empty:
            nb = neighbours[(neighbours["site_id"] == site.id) & neighbours["second_source"]]
            if not nb.empty:
                prep = add_second_source(prep, nb["dx_km"].iloc[0], nb["dy_km"].iloc[0], cfg)
        per = prep["pixel_time_utc"].dt.tz_convert(None).dt.to_period(freq)
        for p, g in prep.groupby(per):
            r = fit_window(g, cfg)
            av = max(pd.Timestamp(g["available_at"].max()),
                     g["pixel_time_utc"].max() + LATENCY["era5t"])
            rows.append({"site_id": site.id, "period": str(p), **r, "available_at": av})
    return pd.DataFrame(rows)


def locate_source(prep: pd.DataFrame, cfg: FitConfig = FitConfig(), half_km: float = 20.0,
                  step_km: float = 2.5) -> dict:
    """Grid-search the source position that best explains the columns.

    Diagnostic for source attribution: the emission is fitted with the source
    moved over a grid of offsets around the registered furnace point; the
    offset with the lowest residual sum of squares is returned. A best
    position far (> ~7 km) from the smelter, or a much larger emission there,
    means the SO2 more likely comes from a neighbouring source (coal power
    plant, refinery, another smelter) than from the smelter itself.
    """
    if prep.empty:
        return {"dx_km": np.nan, "dy_km": np.nan, "offset_km": np.nan, "E_best_kt_yr": np.nan}
    to_kt_yr = M_SO2 / (cfg.tau_h * 3600) * SECONDS_PER_YEAR / 1e6
    y = prep["vcd_mol_km2"].values
    best = (np.inf, 0.0, 0.0, np.nan)
    grid = np.arange(-half_km, half_km + 1e-9, step_km)
    for dx in grid:
        for dy in grid:
            xr_, yr_ = rotate(prep["x_km"].values - dx, prep["y_km"].values - dy,
                              prep["u"].values, prep["v"].values)
            k = plume_kernel(xr_, yr_, prep["wind"].values, cfg)
            X = np.column_stack([k, np.ones(len(k)), prep["x_km"].values, prep["y_km"].values])
            c, *_ = np.linalg.lstsq(X, y, rcond=None)
            rss = float(np.sum((y - X @ c) ** 2))
            if rss < best[0]:
                best = (rss, dx, dy, c[0] * to_kt_yr)
    return {"dx_km": best[1], "dy_km": best[2], "offset_km": float(np.hypot(best[1], best[2])),
            "E_best_kt_yr": best[3]}
