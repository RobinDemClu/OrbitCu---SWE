from datetime import timedelta

import pandas as pd

from orbitcu.ingest.pit import LATENCY, available_at, usable, utc


def test_provider_timestamp_wins():
    av, basis = available_at("2026-06-17T10:25:59Z", "sentinel2_l2a", "2026-06-17T14:24:19Z")
    assert basis == "provider_timestamp" and av == utc("2026-06-17T14:24:19Z")


def test_provider_timestamp_before_acquisition_is_rejected():
    av, basis = available_at("2026-06-17T10:00:00Z", "landsat_c2_l2", "2026-06-01T00:00:00Z")
    assert basis == "latency_rule"
    assert av == utc("2026-06-17T10:00:00Z") + LATENCY["landsat_c2_l2"]


def test_usable_filters_future_records():
    df = pd.DataFrame({"available_at": pd.to_datetime(["2026-01-01", "2026-01-10"], utc=True)})
    assert len(usable(df, "2026-01-05T00:00:00Z")) == 1


def test_era5t_latency():
    av, _ = available_at("2026-01-01T00:00:00Z", "era5t")
    assert av - utc("2026-01-01T00:00:00Z") == timedelta(days=5)
