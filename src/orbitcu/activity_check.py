"""Quick satellite activity check from VIIRS detections (FR-1 grade-C sites).

This is *not* the FR-3 detector: it only answers "was the plant hot in the
last months?" by counting, per calendar month, the number of distinct days
with at least one VIIRS detection within ``radius_km`` of the furnace point.
A primary smelter in operation is detected on a sizeable share of the clear
overpass days every month; a plant that is shut shows zero detections for
consecutive months (cf. Liu et al., 2018 on industrial heat sources).
"""

from __future__ import annotations

import pandas as pd

from .geolocate import haversine_km


def monthly_detection_days(df: pd.DataFrame, lat: float, lon: float, radius_km: float = 1.5,
                           start: str | None = None, end: str | None = None) -> pd.Series:
    """Distinct detection days per month near (lat, lon); months with none are 0."""
    if df.empty:
        near = df
    else:
        near = df[haversine_km(df["latitude"].values, df["longitude"].values, lat, lon) <= radius_km]
    days = pd.to_datetime(near["acq_date"]) if len(near) else pd.Series([], dtype="datetime64[ns]")
    s = days.drop_duplicates().dt.to_period("M").value_counts() if len(days) else pd.Series(dtype=int)
    idx = pd.period_range(start or (days.min() if len(days) else None), end or (days.max() if len(days) else None), freq="M")
    return s.reindex(idx, fill_value=0).sort_index()


def verdict(monthly: pd.Series, last_n: int = 3, min_days: int = 2) -> str:
    """'active' if each of the last ``last_n`` months has >= ``min_days`` detection
    days; 'inactive?' if all of them have zero; otherwise 'intermittent'."""
    tail = monthly.tail(last_n)
    if (tail >= min_days).all():
        return "active"
    if (tail == 0).all():
        return "inactive?"
    return "intermittent"
