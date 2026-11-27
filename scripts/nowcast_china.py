"""Out-of-sample backtest of a monthly nowcast of China's refined copper output (NBS).

For each target month m the nowcast is made on day (end of m + 5 days), the day the satellite
month is complete (ERA5 cloud data latency). Only information available on that day is used:
  * NBS figures already released (release dates from the NBS calendar in official_stats.csv);
  * the weekly satellite status of the 12 watched Chinese smelters for weeks ending in m or
    before (the status itself is point-in-time: trailing 8-week window, reference from the
    three previous years).
Every model is refitted each month on an expanding window of past months whose NBS figure
was released before the nowcast day. January and February are skipped because NBS only
publishes them combined.

Benchmarks (no satellite):
  B1 last value      : last released month (for March: January-February total / 2).
  B2 seasonal+trend  : same month last year x (sum of the last 3 released months /
                       sum of the same 3 months one year earlier).
  B3 naive blend     : expanding-window regression of log(y/B2) on log(B1/B2), i.e. the best
                       mix of B1 and B2 learnt from the past.
Satellite models:
  S1 = B3 + running-share deviation (share of watched Chinese capacity running in m minus its
       median over the previous 12 months, in points).
  S2 = S1 + change in that share versus the same month last year.
  S3 = B2 + running-share deviation only (no B1 term).
Interval: 80 % (and 90 %) band from the empirical quantiles of the model's own past
out-of-sample log errors (at least 12), before that from in-sample residuals (normal).
"""
import os, sys, json
import numpy as np, pandas as pd
from scipy import stats

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from orbitcu.sites import load_sites

D = os.environ.get("ORBITCU_DATA", "/mnt/project-files/data/").rstrip("/") + "/"
# NOWCAST_EXTRA=1: also use the Chinese smelters added from the ICSG list (china_extra_sites.py)
EXTRA = os.environ.get("NOWCAST_EXTRA") == "1"
TAG = "_extra" if EXTRA else ""
OUT = os.path.join(ROOT, "data", "nowcast")
FIG = os.path.join(ROOT, "docs", "latex", "figures")
LAG_DAYS = 5
MIN_TRAIN = 20

# ---------------- official figures ----------------
off = pd.read_csv(os.path.join(ROOT, "data", "reference", "official_stats.csv"))
nbs = off[off.source == "NBS"].copy()
lvl = nbs[nbs.metric == "refined_copper_output_month"].copy()
lvl["month"] = pd.PeriodIndex(lvl.period, freq="M")
lvl["kt"] = lvl.value * 10.0
lvl["pub"] = pd.to_datetime(lvl.published_at)
ytd = nbs[nbs.metric == "refined_copper_output_ytd"].copy()
ytd["month"] = pd.PeriodIndex(ytd.period, freq="M")
jf = ytd[ytd.month.dt.month == 2].copy()            # January-February total
jf["kt"] = jf.value * 10.0 / 2                       # per-month average
jf["pub"] = pd.to_datetime(jf.published_at)
y = lvl.set_index("month").kt
pub = lvl.set_index("month").pub
jf_kt = jf.set_index("month").kt
jf_pub = jf.set_index("month").pub

# ---------------- satellite features ----------------
sites = {s.id: s for s in load_sites()}
if EXTRA:
    class _S:
        def __init__(self, r): self.id, self.capacity_kt, self.country = r.id, r.capacity_kt_cu, "China"
    for r in pd.read_csv(os.path.join(ROOT, "data", "registry", "china_extra_sites.csv")).itertuples():
        sites[r.id] = _S(r)
cn = [k for k, v in sites.items() if v.country == "China"]
st = pd.read_csv(os.path.join(ROOT, "data", "activity_index", "china_extra", "weekly_status_with_extra.csv") if EXTRA
                 else D + "activity_index/weekly_status.csv", parse_dates=["week_end"])
s = st[st.site_id.isin(cn) & (st.status != "unknown")].copy()
s["cap"] = s.site_id.map(lambda x: sites[x].capacity_kt)
s["run"] = (s.status == "running") * s.cap
s["month"] = s.week_end.dt.to_period("M")
g = s.groupby("month").agg(run=("run", "sum"), cap=("cap", "sum"))
share = 100 * g.run / g.cap
share_dev = share - share.shift(1).rolling(12, min_periods=8).median()
share_yoy = share - share.shift(12)
cover = s.groupby(["month", "week_end"]).cap.sum().groupby(level=0).mean() / sum(sites[k].capacity_kt for k in cn)


def known_value(m, day):
    """Last NBS monthly value released before `day` for month m (None if not yet out)."""
    if m.month == 2 or m.month == 1:
        mm = pd.Period(year=m.year, month=2, freq="M")
        if mm in jf_pub.index and jf_pub[mm] < day:
            return jf_kt[mm]
        return None
    if m in pub.index and pub[m] < day:
        return y[m]
    return None


def last_value(m, day):
    p = m - 1
    while p >= m - 6:
        v = known_value(p, day)
        if v is not None:
            return v
        p -= 1
    return None


def seasonal_trend(m, day):
    base = known_value(m - 12, day)
    if base is None:
        return None
    num = den = 0.0; k = 0; p = m - 1
    while k < 3 and p >= m - 10:
        a, b = known_value(p, day), known_value(p - 12, day)
        if a is not None and b is not None and p.month != 1:
            num += a; den += b; k += 1
        p -= 1
    return base * num / den if k == 3 else None


rows = []
for m in y.index:
    day = m.end_time.normalize() + pd.Timedelta(days=LAG_DAYS)
    rows.append(dict(month=m, nowcast_day=day, release=pub[m], y=y[m], B1=last_value(m, day),
                     B2=seasonal_trend(m, day), share=share.get(m), share_dev=share_dev.get(m),
                     share_yoy=share_yoy.get(m), coverage=cover.get(m)))
F = pd.DataFrame(rows).set_index("month")
F["lead_days"] = (F.release - F.nowcast_day).dt.days
F = F.dropna(subset=["B1", "B2"])
F["z"] = np.log(F.y / F.B2)
F["x_b1"] = np.log(F.B1 / F.B2)

MODELS = {"B3": ["x_b1"], "S1": ["x_b1", "share_dev"], "S2": ["x_b1", "share_dev", "share_yoy"],
          "S3": ["share_dev"]}
SAT_START = F.share_dev.first_valid_index()
E = F[F.index >= SAT_START].dropna(subset=["share_dev", "share_yoy"]).copy()

pred = {k: {} for k in ["B1", "B2", *MODELS]}
coefs = []
for i, m in enumerate(E.index):
    day = E.nowcast_day[m]
    tr = E[(E.index < m) & (E.release < day)]
    if len(tr) < MIN_TRAIN:
        continue
    pred["B1"][m] = (E.B1[m], None)
    pred["B2"][m] = (E.B2[m], None)
    for name, cols in MODELS.items():
        X = np.column_stack([np.ones(len(tr))] + [tr[c].to_numpy() for c in cols])
        beta, *_ = np.linalg.lstsq(X, tr.z.to_numpy(), rcond=None)
        res = tr.z.to_numpy() - X @ beta
        sd = res.std(ddof=X.shape[1])
        x = np.r_[1.0, [E[c][m] for c in cols]]
        pred[name][m] = (E.B2[m] * np.exp(x @ beta), sd)
        coefs.append(dict(month=str(m), model=name, **{c: b for c, b in zip(["const", *cols], beta)}))

# assemble with intervals from past out-of-sample errors
out = []
for name, d in pred.items():
    past_err = []
    for m in sorted(d):
        hat, sd = d[m]
        if len(past_err) >= 12:
            q = np.quantile(past_err, [0.10, 0.90, 0.05, 0.95])
        else:
            if sd is None:
                sd = np.std(past_err, ddof=1) if len(past_err) >= 5 else 0.05
            q = np.array([-1.2816, 1.2816, -1.6449, 1.6449]) * sd
        # only errors of months already released at this nowcast day may be used next time
        e = np.log(E.y[m] / hat)
        out.append(dict(month=str(m), model=name, nowcast_kt=hat, actual_kt=E.y[m],
                        lo80=hat * np.exp(q[0]), hi80=hat * np.exp(q[1]), lo90=hat * np.exp(q[2]), hi90=hat * np.exp(q[3]),
                        prev_kt=E.B1[m], B2=E.B2[m], nowcast_day=E.nowcast_day[m].date(), release=E.release[m].date()))
        past_err.append(e)
R = pd.DataFrame(out)
R["err_kt"] = R.nowcast_kt - R.actual_kt
R["ape"] = 100 * R.err_kt.abs() / R.actual_kt
R["dir_hit"] = np.sign(R.nowcast_kt - R.prev_kt) == np.sign(R.actual_kt - R.prev_kt)
R["surp_hit"] = np.sign(R.nowcast_kt - R.B2) == np.sign(R.actual_kt - R.B2)
R["in80"] = (R.actual_kt >= R.lo80) & (R.actual_kt <= R.hi80)
R["in90"] = (R.actual_kt >= R.lo90) & (R.actual_kt <= R.hi90)


def dm(a, b):
    """Diebold-Mariano on absolute errors (a better than b if stat < 0), HAC lag 2."""
    d = a.abs().to_numpy() - b.abs().to_numpy(); n = len(d); dbar = d.mean()
    gam = [np.mean((d[k:] - dbar) * (d[:n - k] - dbar)) for k in range(3)]
    v = (gam[0] + 2 * sum((1 - k / 3) * gam[k] for k in (1, 2))) / n
    t = dbar / np.sqrt(v); return t, 2 * stats.t.sf(abs(t), n - 1)


summ = []
ref = R[R.model == "B3"].set_index("month").err_kt
for name, d in R.groupby("model", sort=False):
    d = d.set_index("month")
    nz = d.prev_kt != d.actual_kt
    t, p = dm(d.err_kt, ref) if name != "B3" else (np.nan, np.nan)
    sh = d.surp_hit if name not in ("B2",) else pd.Series(np.nan, index=d.index)
    summ.append(dict(model=name, n=len(d), first=d.index[0], last=d.index[-1],
                     mae_kt=d.err_kt.abs().mean(), mape_pct=d.ape.mean(), rmse_kt=np.sqrt((d.err_kt ** 2).mean()),
                     bias_kt=d.err_kt.mean(), dir_hit=d.dir_hit[nz].mean() if name != "B1" else np.nan, surprise_sign_hit=sh.mean(),
                     cover80=d.in80.mean(), cover90=d.in90.mean(), width80_kt=(d.hi80 - d.lo80).mean(),
                     dm_t_vs_B3=t, dm_p_vs_B3=p))
S = pd.DataFrame(summ)
# by period (two halves) for robustness
half = []
for name, d in R.groupby("model", sort=False):
    for lab, mask in [("first half", d.month < "2024-07"), ("second half", d.month >= "2024-07")]:
        x = d[mask]
        half.append(dict(model=name, period=lab, n=len(x), mape_pct=x.ape.mean(), mae_kt=x.err_kt.abs().mean(),
                         dir_hit=x.dir_hit.mean()))
H = pd.DataFrame(half)
# does the satellite deviation explain the naive error at all? (out-of-sample months)
b3 = R[R.model == "B3"].set_index("month")
naive_err = np.log(b3.actual_kt / b3.nowcast_kt)
sd_ = E.share_dev.rename(index=str).reindex(naive_err.index)
rho = stats.spearmanr(sd_, naive_err)
big = naive_err.abs() >= naive_err.abs().quantile(0.75)



def oos_mae(dev):
    """Out-of-sample MAE (kt) of S1 when the satellite deviation series is replaced by `dev`."""
    err = []
    for m in E.index:
        tr = E[(E.index < m) & (E.release < E.nowcast_day[m])]
        if len(tr) < MIN_TRAIN:
            continue
        X = np.column_stack([np.ones(len(tr)), tr.x_b1, dev[tr.index]])
        beta, *_ = np.linalg.lstsq(X, tr.z.to_numpy(), rcond=None)
        err.append(abs(E.B2[m] * np.exp(np.r_[1.0, E.x_b1[m], dev[m]] @ beta) - E.y[m]))
    return np.mean(err)


# placebo: circular shifts of the satellite series (keeps its autocorrelation, breaks timing)
real = oos_mae(E.share_dev)
n = len(E); shifts = [k for k in range(6, n - 6)]
plac = np.array([oos_mae(pd.Series(np.roll(E.share_dev.to_numpy(), k), index=E.index)) for k in shifts])
placebo = dict(real_mae_kt=real, placebo_median_mae_kt=float(np.median(plac)), n_shifts=len(shifts),
               p_value=float(np.mean(plac <= real)))
print("placebo", placebo)

os.makedirs(OUT, exist_ok=True)
R.round(3).to_csv(os.path.join(OUT, "china_nowcast_backtest" + TAG + ".csv"), index=False)
S.round(4).to_csv(os.path.join(OUT, "china_nowcast_summary" + TAG + ".csv"), index=False)
H.round(3).to_csv(os.path.join(OUT, "china_nowcast_halves" + TAG + ".csv"), index=False)
pd.DataFrame(coefs).round(5).to_csv(os.path.join(OUT, "china_nowcast_coefficients" + TAG + ".csv"), index=False)
F.drop(columns=["z"]).round(4).to_csv(os.path.join(OUT, "china_nowcast_inputs" + TAG + ".csv"))
extra = dict(spearman_share_dev_vs_B3_error=rho.statistic, p=rho.pvalue, n=int(naive_err.notna().sum()),
             big_miss_months=int(big.sum()),
             big_miss_push_right_S1=float((np.sign(R[R.model == "S1"].set_index("month").nowcast_kt - b3.nowcast_kt)
                                    == np.sign(b3.actual_kt - b3.nowcast_kt))[big].mean()),
             big_miss_mean_push_kt=float((R[R.model == "S1"].set_index("month").nowcast_kt - b3.nowcast_kt)[big].abs().mean()),
             big_miss_mean_miss_kt=float((b3.actual_kt - b3.nowcast_kt)[big].abs().mean()),
             median_lead_days=float(E.lead_days.median()), placebo=placebo, mean_coverage=float(E.coverage.mean()))
json.dump(extra, open(os.path.join(OUT, "china_nowcast_extra" + TAG + ".json"), "w"), indent=1, default=float)
pd.set_option("display.width", 220)
print(S.round(3).to_string()); print(H.round(2).to_string()); print(extra)

# ---------------- figures ----------------
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
C = {"S1": "#1f5fa8", "B3": "#888888", "B1": "#c0392b", "B2": "#d68910"}
d = R[R.model == "S1"].copy(); t = pd.PeriodIndex(d.month, freq="M").to_timestamp()
fig, ax = plt.subplots(2, 1, figsize=(10, 5.6), sharex=True, gridspec_kw=dict(height_ratios=[3, 2]))
ax[0].fill_between(t, d.lo80, d.hi80, color=C["S1"], alpha=0.15, lw=0, label="80 % band (satellite model)")
ax[0].plot(t, d.actual_kt, color="black", lw=1.6, marker="o", ms=2.5, label="NBS published")
ax[0].plot(t, d.nowcast_kt, color=C["S1"], lw=1.3, label="Nowcast with satellite (S1)")
b = R[R.model == "B3"]; ax[0].plot(t, b.nowcast_kt.to_numpy(), color=C["B3"], lw=1, ls="--", label="Naive blend (B3)")
ax[0].set_ylabel("kt per month"); ax[0].legend(fontsize=7.5, frameon=False, ncol=2, loc="upper left")
ax[0].set_title("China refined copper: nowcast made 5 days after month end vs NBS release", fontsize=9, loc="left")
for name in ["B1", "B2", "B3", "S1"]:
    x = R[R.model == name]
    ax[1].plot(t, x.ape.rolling(12, min_periods=6).mean().to_numpy(), color=C[name], lw=1.4 if name == "S1" else 1, label=name)
ax[1].set_ylabel("% abs. error\n(12-month mean)"); ax[1].legend(fontsize=7.5, frameon=False, ncol=4)
for a in ax: a.spines[["top", "right"]].set_visible(False); a.grid(axis="y", color="#eee")
plt.tight_layout(); plt.savefig(os.path.join(FIG, "china_nowcast_backtest" + TAG + ".png"), dpi=160); plt.close()

fig, ax = plt.subplots(1, 2, figsize=(10, 3.6))
ax[0].scatter(sd_, 100 * naive_err, s=14, color=C["S1"])
ax[0].axhline(0, color="#999", lw=0.6); ax[0].axvline(0, color="#999", lw=0.6)
ax[0].set_xlabel("Satellite running-share deviation (points)"); ax[0].set_ylabel("NBS vs naive blend (%)")
ax[0].set_title(f"Satellite signal vs naive miss (Spearman {rho.statistic:.2f}, p={rho.pvalue:.2f})", fontsize=8.5, loc="left")
lab = ["B1", "B2", "B3", "S1", "S2", "S3"]
v = S.set_index("model").loc[lab]
ax[1].bar(lab, v.mape_pct, color=[C.get(k, "#7fa7d6") for k in lab])
for i, k in enumerate(lab): ax[1].text(i, v.mape_pct[k] + 0.05, f"{v.mape_pct[k]:.2f}", ha="center", fontsize=8)
ax[1].set_ylabel("Mean abs. error (%)"); ax[1].set_title("Out-of-sample error by model", fontsize=8.5, loc="left")
for a in ax: a.spines[["top", "right"]].set_visible(False)
plt.tight_layout(); plt.savefig(os.path.join(FIG, "china_nowcast_models" + TAG + ".png"), dpi=160); plt.close()
