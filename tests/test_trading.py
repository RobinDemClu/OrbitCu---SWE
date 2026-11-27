import numpy as np
import pandas as pd

from orbitcu import trading as T


def _weekly(values, start="2020-01-06"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="7D"), dtype=float)


def test_weekly_returns_forward_looking_one_week():
    p = _weekly([100, 110, 99])
    r = T.weekly_returns(p)
    assert np.isclose(r.iloc[0], 0.10) and np.isclose(r.iloc[1], -0.10) and np.isnan(r.iloc[2])


def test_surprise_excludes_current_week():
    s = pd.Series([0.8] * 30 + [0.5], index=pd.date_range("2020-01-05", periods=31, freq="7D"))
    sur = T.supply_surprise(s, lookback=26)
    assert np.isclose(sur.iloc[-1], -0.3)   # median of the past stays 0.8


def test_low_supply_gives_long_position():
    z = pd.Series([-4.0, -1.0, 0.0, 3.0])
    raw = T.raw_position(z, z_full=2.0)
    assert list(raw) == [1.0, 0.5, -0.0, -1.0]


def test_signal_traded_after_lag():
    sig = pd.Series([1.0], index=[pd.Timestamp("2024-03-03")])   # Sunday
    out = T.to_trade_dates(sig, lag_weeks=1)
    assert out.index[0] == pd.Timestamp("2024-03-11")             # Monday one week later


def test_vol_uses_only_past_returns():
    r = _weekly([0.01, -0.01] * 20 + [0.5])
    v = T.past_vol(r, window=26)
    assert v.iloc[-1] < 0.2                                        # the big last return is not in its own vol


def test_backtest_costs():
    w = _weekly([1.0, 1.0, -1.0])
    r = _weekly([0.01, 0.01, 0.01])
    bt = T.backtest(w, r, cost_bps=2)
    assert np.allclose(bt.turnover, [1, 0, 2])
    assert np.allclose(bt.net, [0.01 - 0.0002, 0.01, -0.01 - 0.0004])
