from datetime import date

import pandas as pd

from orbitcu.ingest import firms


def test_date_chunks_cover_range_exactly():
    chunks = list(firms.date_chunks(date(2026, 1, 1), date(2026, 1, 12)))
    assert chunks == [(date(2026, 1, 1), 5), (date(2026, 1, 6), 5), (date(2026, 1, 11), 2)]


def test_plan_switches_sp_to_nrt(monkeypatch):
    avail = pd.DataFrame(
        {"min_date": pd.to_datetime(["2012-01-20", "2026-08-01"]),
         "max_date": pd.to_datetime(["2026-07-31", "2026-11-02"])},
        index=["VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"])
    monkeypatch.setattr(firms, "data_availability", lambda: avail)
    plan = firms.plan_requests("VIIRS_SNPP", date(2026, 7, 28), date(2026, 8, 3))
    assert plan[0] == ("VIIRS_SNPP_SP", date(2026, 7, 28), 4)
    assert plan[1] == ("VIIRS_SNPP_NRT", date(2026, 8, 1), 3)


def test_normalise_pit_columns():
    df = pd.DataFrame({"latitude": [1.0], "longitude": [2.0], "acq_date": ["2026-03-01"], "acq_time": [107],
                       "confidence": ["n"], "satellite": ["N"], "version": ["2.0NRT"], "source": ["VIIRS_SNPP_NRT"]})
    out = firms.normalise(df)
    assert str(out["acquired_at"][0]) == "2026-03-01 01:07:00+00:00"
    assert (out["available_at"] - out["acquired_at"])[0] == pd.Timedelta(hours=3)
    assert out["product"][0] == "NRT"
