"""Weekly copper trading strategy driven by the satellite supply surprise.

Idea: when the satellites show less smelter capacity running than normal, refined copper
supply is lower than the market expects, which should push the price up (and the reverse).

Data
  * signal: the weekly capacity-running index (activity_index.capacity_index), one value per
    week ending Sunday;
  * prices: one close per week (first trading day of the week, normally the Monday) for
    COMEX HG front-month (USD/lb) and, as a check, LME copper 3-month (USD/t).

Rules (fixed before looking at the test period)
  1. supply surprise s_t = running share at week t minus its median over the previous
     ``lookback`` weeks (negative = less supply than usual);
  2. z_t = s_t / standard deviation of s over the same past window, optionally averaged
     over the last ``smooth`` weeks;
  3. raw position = clip(-z_t / ``z_full``, -1, 1): long when supply is short, short when
     supply is abundant, full size at |z| = z_full;
  4. volatility targeting: weight = raw x target_vol / sigma, sigma = past 26-week volatility
     of the instrument, |weight| <= ``max_weight`` (no leverage above 1 by default);
  5. the signal for the week ending Sunday t is traded at the close of the Monday
     ``lag_weeks`` weeks later (default 1: ERA5 cloud data arrive about 5 days late), and
     held one week;
  6. costs: ``cost_bps`` x |change in weight| at each rebalance.
Everything at time t uses data available at t only (trailing windows, shifted).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WEEKS_PER_YEAR = 52


# ---------------------------------------------------------------- data
def load_weekly_prices(path: str, column: str) -> pd.Series:
    df = pd.read_csv(path, parse_dates=["week_monday"])
    return df.set_index("week_monday")[column].astype(float).rename(column)


def weekly_returns(price: pd.Series) -> pd.Series:
    """Return earned by a position opened at Monday m and closed at Monday m+7, indexed by m."""
    return (price.shift(-1) / price - 1).rename("ret")


# ---------------------------------------------------------------- signal
def supply_surprise(running_share: pd.Series, lookback: int = 52, min_frac: float = 0.6) -> pd.Series:
    """Running share minus its trailing median (previous ``lookback`` weeks, current excluded)."""
    past = running_share.shift(1).rolling(lookback, min_periods=int(lookback * min_frac))
    return (running_share - past.median()).rename("surprise")


def surprise_z(running_share: pd.Series, lookback: int = 52, smooth: int = 1,
               min_frac: float = 0.6) -> pd.Series:
    s = supply_surprise(running_share, lookback, min_frac)
    sd = s.shift(1).rolling(lookback, min_periods=int(lookback * min_frac)).std()
    z = (s / sd.replace(0, np.nan))
    if smooth > 1:
        z = z.rolling(smooth, min_periods=1).mean()
    return z.rename("z")


def raw_position(z: pd.Series, z_full: float = 2.0) -> pd.Series:
    return (-z / z_full).clip(-1, 1).rename("raw")


def to_trade_dates(sig: pd.Series, lag_weeks: int = 1) -> pd.Series:
    """Move a signal dated on Sunday week ends to the Monday it can be traded."""
    out = sig.copy()
    out.index = out.index + pd.Timedelta(days=1 + 7 * lag_weeks)
    return out


# ---------------------------------------------------------------- portfolio
def past_vol(ret: pd.Series, window: int = 26) -> pd.Series:
    """Annualised volatility known at Monday m: returns of the weeks ending at or before m."""
    return (ret.shift(1).rolling(window, min_periods=window // 2).std() * np.sqrt(WEEKS_PER_YEAR)).rename("vol")


def vol_target_weight(raw: pd.Series, ret: pd.Series, target_vol: float = 0.10,
                      max_weight: float = 1.0, window: int = 26) -> pd.Series:
    vol = past_vol(ret, window).reindex(raw.index)
    return (raw * target_vol / vol).clip(-max_weight, max_weight).rename("weight")


def backtest(weight: pd.Series, ret: pd.Series, cost_bps: float = 2.0) -> pd.DataFrame:
    df = pd.concat([weight.rename("weight"), ret.rename("ret")], axis=1).dropna(subset=["ret"])
    df["weight"] = df["weight"].fillna(0.0)
    df["turnover"] = df["weight"].diff().abs().fillna(df["weight"].abs())
    df["gross"] = df["weight"] * df["ret"]
    df["cost"] = df["turnover"] * cost_bps / 1e4
    df["net"] = df["gross"] - df["cost"]
    return df


def metrics(net: pd.Series, turnover: pd.Series | None = None) -> dict:
    net = net.dropna()
    if len(net) < 2:
        return {}
    ann_ret = (1 + net).prod() ** (WEEKS_PER_YEAR / len(net)) - 1
    vol = net.std() * np.sqrt(WEEKS_PER_YEAR)
    eq = (1 + net).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    out = {"weeks": len(net), "ann_return": ann_ret, "ann_vol": vol,
           "sharpe": net.mean() / net.std() * np.sqrt(WEEKS_PER_YEAR) if net.std() > 0 else np.nan,
           "max_drawdown": dd, "hit_rate": (net[net != 0] > 0).mean()}
    if turnover is not None:
        out["turnover_per_year"] = turnover.reindex(net.index).sum() * WEEKS_PER_YEAR / len(net)
    return out


def sharpe_ci(net: pd.Series, block: int = 8, n_boot: int = 2000, seed: int = 0) -> tuple[float, float]:
    """95% moving-block bootstrap interval of the annualised Sharpe ratio."""
    x = net.dropna().to_numpy()
    n = len(x)
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n_boot):
        starts = rng.integers(0, n - block + 1, size=n // block + 1)
        s = np.concatenate([x[k:k + block] for k in starts])[:n]
        if s.std() > 0:
            out.append(s.mean() / s.std() * np.sqrt(WEEKS_PER_YEAR))
    lo, hi = np.percentile(out, [2.5, 97.5])
    return float(lo), float(hi)


def run_strategy(running_share: pd.Series, price: pd.Series, lookback: int = 52, smooth: int = 1,
                 z_full: float = 2.0, lag_weeks: int = 1, target_vol: float = 0.10,
                 max_weight: float = 1.0, cost_bps: float = 2.0) -> pd.DataFrame:
    ret = weekly_returns(price)
    z = surprise_z(running_share, lookback, smooth)
    raw = to_trade_dates(raw_position(z, z_full), lag_weeks).reindex(ret.index)
    w = vol_target_weight(raw, ret, target_vol, max_weight)
    bt = backtest(w, ret, cost_bps)
    bt["raw"] = raw
    return bt


def buy_and_hold(price: pd.Series, target_vol: float | None = None, cost_bps: float = 2.0) -> pd.DataFrame:
    """Long one unit all the time (target_vol=None), or long scaled to a volatility target."""
    ret = weekly_returns(price)
    if target_vol is None:
        w = pd.Series(1.0, index=ret.index)
    else:
        w = vol_target_weight(pd.Series(1.0, index=ret.index), ret, target_vol)
    return backtest(w, ret, cost_bps)
