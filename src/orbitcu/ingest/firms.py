"""NASA FIRMS active-fire / thermal-anomaly client (VIIRS 375 m, MODIS 1 km).

API: ``https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{source}/
{west,south,east,north}/{day_range}/{start_date}`` with ``day_range`` in 1..5.
A MAP_KEY allows 5000 transactions per 10 minutes.

Two product families exist per sensor:

* ``*_SP`` (standard processing, science quality) - complete archive, but
  released months after acquisition;
* ``*_NRT`` (near real time) - available ~3 h after overpass, kept online for
  the recent period only.

:func:`fetch_area` picks SP where it exists and NRT for the most recent
months (using the provider's ``data_availability`` endpoint), so a backfill is
seamless. All records get ``available_at = acquired_at + 3 h`` (the NRT
latency): an SP detection is the reprocessed version of a detection that was
published in NRT within hours. This is a documented approximation - the NRT
and SP detection sets differ slightly (see ``docs/ingestion.md``).
"""

from __future__ import annotations

import io
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd
import requests

from .pit import LATENCY

BASE = "https://firms.modaps.eosdis.nasa.gov"
MAX_DAY_RANGE = 5
VIIRS_SENSORS = ("VIIRS_SNPP", "VIIRS_NOAA20", "VIIRS_NOAA21")


def _key() -> str:
    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        raise RuntimeError("FIRMS_MAP_KEY is not set")
    return key


def transactions_used() -> tuple[int, int]:
    """(current_transactions, transaction_limit) for the MAP_KEY's 10-min window."""
    r = requests.get(f"{BASE}/mapserver/mapkey_status/?MAP_KEY={_key()}", timeout=60)
    j = r.json()
    return int(j.get("current_transactions", 0)), int(j.get("transaction_limit", 5000))


def _backoff() -> None:
    """Pause after FIRMS refuses a request for exceeding the 5000-transactions
    per 10-minute budget (it answers HTTP 400 "Invalid MAP_KEY"). The
    ``mapkey_status`` counter is not a usable gate: it can stay near the limit
    while requests are accepted again, so we simply wait and retry."""
    time.sleep(60)


def _get(url: str, retries: int = 15) -> str:
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=120)
        except requests.ConnectionError:
            time.sleep(2 ** min(attempt, 6))
            continue
        if r.status_code == 200 and not r.text.startswith("Invalid"):
            if "Exceeding allowed transaction limit" in r.text:
                _backoff()
                continue
            return r.text
        if "Invalid MAP_KEY" in r.text:  # also returned when the budget is exhausted
            _backoff()
            continue
        time.sleep(2 ** min(attempt, 6))
    # never echo the URL: it contains the MAP_KEY
    raise RuntimeError(f"FIRMS request failed after {retries} attempts")


@lru_cache(maxsize=1)
def data_availability() -> pd.DataFrame:
    txt = _get(f"{BASE}/api/data_availability/csv/{_key()}/ALL")
    df = pd.read_csv(io.StringIO(txt), parse_dates=["min_date", "max_date"])
    return df.set_index("data_id")


def date_chunks(start: date, end: date, step: int = MAX_DAY_RANGE):
    """Yield ``(chunk_start, n_days)`` covering [start, end] inclusive."""
    d = start
    while d <= end:
        n = min(step, (end - d).days + 1)
        yield d, n
        d += timedelta(days=n)


def plan_requests(sensor: str, start: date, end: date) -> list[tuple[str, date, int]]:
    """Split [start, end] into (source, chunk_start, n_days) using SP then NRT."""
    avail = data_availability()
    out: list[tuple[str, date, int]] = []
    sp, nrt = f"{sensor}_SP", f"{sensor}_NRT"
    sp_end = avail.loc[sp, "max_date"].date() if sp in avail.index else start - timedelta(days=1)
    if start <= sp_end:
        out += [(sp, d, n) for d, n in date_chunks(max(start, avail.loc[sp, "min_date"].date()), min(end, sp_end))]
    if end > sp_end and nrt in avail.index:
        nrt_start = max(sp_end + timedelta(days=1), avail.loc[nrt, "min_date"].date(), start)
        out += [(nrt, d, n) for d, n in date_chunks(nrt_start, min(end, avail.loc[nrt, "max_date"].date()))]
    return out


def _fetch_one(source: str, bbox, day: date, n: int) -> pd.DataFrame:
    w, s, e, n_ = bbox
    url = f"{BASE}/api/area/csv/{_key()}/{source}/{w},{s},{e},{n_}/{n}/{day.isoformat()}"
    txt = _get(url)
    if not txt.strip() or txt.count("\n") < 1:
        return pd.DataFrame()
    df = pd.read_csv(io.StringIO(txt))
    if df.empty:
        return df
    df["source"] = source
    return df


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Add UTC ``acquired_at`` / ``available_at`` columns and harmonise types."""
    if df.empty:
        return df
    hhmm = df["acq_time"].astype(int).astype(str).str.zfill(4)
    df["acquired_at"] = pd.to_datetime(df["acq_date"] + " " + hhmm, format="%Y-%m-%d %H%M", utc=True)
    df["available_at"] = df["acquired_at"] + LATENCY["firms_nrt"]
    df["pit_basis"] = "latency_rule:firms_nrt"
    df["product"] = df["source"].str.rsplit("_", n=1).str[1]
    df["confidence"] = df["confidence"].astype(str)
    df["version"] = df["version"].astype(str)  # SP: "2", NRT: "2.0NRT"
    if "type" not in df:
        df["type"] = pd.NA  # NRT files have no 'type' column
    df["type"] = pd.to_numeric(df["type"], errors="coerce").astype("Int8")
    df["ingested_at"] = datetime.now(timezone.utc)
    return df


def fetch_area(bbox, start: date, end: date, sensors=VIIRS_SENSORS, workers: int = 4) -> pd.DataFrame:
    """All detections inside ``bbox`` (W, S, E, N) between two dates, inclusive."""
    jobs = [(src, d, n) for s in sensors for (src, d, n) in plan_requests(s, start, end)]
    with ThreadPoolExecutor(workers) as ex:
        parts = list(ex.map(lambda j: _fetch_one(j[0], bbox, j[1], j[2]), jobs))
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df = normalise(df)
    return df.drop_duplicates(subset=["latitude", "longitude", "acquired_at", "satellite"]).reset_index(drop=True)
