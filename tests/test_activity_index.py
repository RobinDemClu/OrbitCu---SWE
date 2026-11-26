import numpy as np
import pandas as pd

from orbitcu import activity_index as A


class _Site:
    def __init__(self, id, cap=100.0):
        self.id, self.capacity_kt, self.lon, self.lat = id, cap, 0.0, 0.0


def _daily(site, hot_fn, start="2019-01-01", end="2022-12-31"):
    days = pd.date_range(start, end, freq="D")
    return pd.DataFrame({"site_id": site, "day": days, "hot": [hot_fn(d) for d in days],
                         "clear": True, "has_cloud": True})


def test_stop_is_detected_and_dated_after_it_happens():
    stop0, stop1 = pd.Timestamp("2022-03-01"), pd.Timestamp("2022-06-30")
    d = _daily("A", lambda t: not (stop0 <= t <= stop1) and t.dayofweek < 5)   # hot 5 days a week
    st = A.weekly_status(d, reference="trailing")
    ev = A.detect_events(st, min_stop_weeks=3, latency_days=5)
    stops = ev[ev.event == "stop"]
    assert len(stops) == 1
    assert stops.iloc[0].seen_date > stop0                     # never before the stop starts
    assert (stops.iloc[0].seen_date - stop0).days < 60   # 8-week window: seen after ~6 weeks
    assert (ev.event == "restart").sum() == 1


def test_seasonal_reference_ignores_a_stop_that_repeats_every_year():
    # the plant (or the cloud artefact) is dark every August
    d = _daily("A", lambda t: t.month != 8)
    st_season = A.weekly_status(d, reference="seasonal")
    st_trail = A.weekly_status(d, reference="trailing")
    n22 = lambda s: int(((s.week_end.dt.year == 2022) & (s.status == "stopped")).sum())
    assert n22(st_trail) >= 4          # the trailing reference flags every August
    assert n22(st_season) <= 1         # the seasonal one keeps at most an edge week


def test_capacity_index_counts_start_up_as_stopped_before_start():
    weeks = pd.date_range("2022-01-02", periods=4, freq="W-SUN")
    st = pd.DataFrame({"site_id": ["A"] * 4 + ["B"] * 4, "week_end": list(weeks) * 2,
                       "status": ["running"] * 4 + ["unknown", "unknown", "running", "running"]})
    idx = A.capacity_index(st, [_Site("A", 100), _Site("B", 300)], start_dates={"B": "2022-01-15"})
    assert np.allclose(idx.running_share, [0.25, 0.25, 1.0, 1.0])
    assert np.allclose(idx.coverage, [1.0, 1.0, 1.0, 1.0])
