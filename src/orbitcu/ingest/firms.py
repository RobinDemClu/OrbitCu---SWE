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
and SP detection sets differ slightly (see the project report, ``docs/latex/orbitcu_report.pdf``).
"""

from __future__ import annotations

import io
import os
import threading
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


_quota_lock = threading.Lock()
_quota_until = 0.0  # time.time() before which no thread sends a request
QUOTA_WAIT_S = 60
QUOTA_MAX_WAIT_S = 15 * 60  # the FIRMS window is 10 minutes


def _wait_quota() -> None:
    """Block while a quota pause is running (shared by all threads)."""
    while True:
        with _quota_lock:
            left = _quota_until - time.time()
        if left <= 0:
            return
        time.sleep(min(left, 5))


def _backoff() -> None:
    """FIRMS refused a request for exceeding the 5000-transactions per 10-minute
    budget (it answers HTTP 400 "Invalid MAP_KEY" or "Exceeding allowed
    transaction limit"). Pause *all* threads for QUOTA_WAIT_S, so the parallel
    downloads stop hammering the server together. The ``mapkey_status`` counter
    is not a usable gate: it can stay near the limit while requests are
    accepted again, so we simply wait and retry."""
    global _quota_until
    with _quota_lock:
        if _quota_until <= time.time():
            print(f"\nFIRMS quota reached: pausing {QUOTA_WAIT_S} s, then resuming.", flush=True)
            _quota_until = time.time() + QUOTA_WAIT_S
    _wait_quota()


def _get(url: str, retries: int = 8) -> str:
    """GET with retries. Quota refusals wait (up to QUOTA_MAX_WAIT_S in total)
    and do not count as failed attempts; other errors are retried ``retries`` times."""
    errors, quota_waited = 0, 0.0
    while True:
        _wait_quota()
        try:
            r = requests.get(url, timeout=60)
        except (requests.ConnectionError, requests.Timeout):
            r = None
        if r is not None:
            quota = "Invalid MAP_KEY" in r.text or "Exceeding allowed transaction limit" in r.text
            if r.status_code == 200 and not quota and not r.text.startswith("Invalid"):
                return r.text
            if quota:
                if quota_waited >= QUOTA_MAX_WAIT_S:
                    break
                t = time.time()
                _backoff()
                quota_waited += time.time() - t
                continue
        errors += 1
        if errors >= retries:
            break
        time.sleep(min(2 ** errors, 10))
    # never echo the URL: it contains the MAP_KEY
    raise RuntimeError("FIRMS request failed (quota still exhausted or server unreachable)")


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


# ------------------------------------------------------------ regional backfill
def region_boxes(sites, pad_deg: float = 0.1, max_span_deg: float = 16.0) -> dict[str, tuple]:
    """Group sites into a few bounding boxes (one FIRMS request per box and 5 days).

    A multi-year backfill is limited by the number of requests (FIRMS answers
    each in ~1-10 s), not by data volume, so neighbouring sites share a box.
    Greedy: sites sorted by longitude join the current box while it stays
    within ``max_span_deg`` in both directions.
    """
    boxes, cur = {}, []

    def close(group):
        w = min(s.lon for s in group) - pad_deg
        e = max(s.lon for s in group) + pad_deg
        so = min(s.lat for s in group) - pad_deg
        n = max(s.lat for s in group) + pad_deg
        boxes["+".join(s.id for s in group)] = (round(w, 3), round(so, 3), round(e, 3), round(n, 3))

    for s in sorted(sites, key=lambda x: x.lon):
        trial = cur + [s]
        if cur and (max(x.lon for x in trial) - min(x.lon for x in trial) > max_span_deg
                    or max(x.lat for x in trial) - min(x.lat for x in trial) > max_span_deg):
            close(cur)
            trial = [s]
        cur = trial
    if cur:
        close(cur)
    return boxes


def fetch_regions(out_dir, boxes: dict, start: str, end: str, sensor: str = "VIIRS_SNPP",
                  workers: int = 4) -> list[str]:
    """Download all detections in each box, month by month, into
    ``out_dir/<box_name>/<YYYY-MM>.parquet``. Months on disk are skipped; a
    month with a failed request is not written (rerun to fill it)."""
    import sys
    from concurrent.futures import as_completed
    from pathlib import Path

    out_dir = Path(out_dir)
    months = [str(p) for p in pd.period_range(start, end, freq="M")]
    p0, p1 = pd.Period(start, "M"), pd.Period(end, "M")
    plan = plan_requests(sensor, p0.start_time.date(), p1.end_time.date())
    todo, jobs = [], []
    for name, bbox in boxes.items():
        for m in months:
            path = out_dir / name / f"{m}.parquet"
            # a month that has not ended (plus 2 days of latency) is fetched again next time
            done = pd.Period(m, "M").end_time + pd.Timedelta(days=2) < pd.Timestamp.utcnow().tz_localize(None)
            if path.exists() and done:
                continue
            k = len(todo)
            todo.append((name, m, path))
            p = pd.Period(m, "M")
            ms, me = p.start_time.date(), p.end_time.date()
            for src, d, n in plan:
                d0, d1 = max(d, ms), min(d + timedelta(days=n - 1), me)
                if d0 <= d1:
                    jobs.append((k, src, bbox, d0, (d1 - d0).days + 1))
    print(f"{sensor}: {len(todo)} box-months, {len(jobs)} requests", flush=True)
    parts = {k: [] for k in range(len(todo))}
    left = {k: 0 for k in range(len(todo))}
    for j in jobs:
        left[j[0]] += 1
    failed, done, t0 = set(), 0, time.time()
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(_fetch_one, src, bbox, d, n): k for k, src, bbox, d, n in jobs}
        for f in as_completed(futs):
            k = futs[f]
            try:
                parts[k].append(f.result())
            except Exception:
                failed.add(k)
            left[k] -= 1
            done += 1
            if left[k] == 0:
                name, m, path = todo[k]
                got = [p for p in parts.pop(k) if not p.empty]
                if k not in failed:
                    df = normalise(pd.concat(got, ignore_index=True)) if got else pd.DataFrame()
                    path.parent.mkdir(parents=True, exist_ok=True)
                    df.to_parquet(path)
            if done % 50 == 0 or done == len(jobs):
                el = time.time() - t0
                sys.stdout.write(f"{done}/{len(jobs)} requests, ~{el / done * (len(jobs) - done) / 60:.0f} min left\n")
                sys.stdout.flush()
    return [f"{todo[k][0]} {todo[k][1]}" for k in sorted(failed)]


def split_regions(region_dir, site_dir, sites, boxes: dict) -> None:
    """Write per-site month caches (``site_dir/<site_id>/<YYYY-MM>.parquet``,
    detections inside the site's thermal box) from the regional downloads."""
    from pathlib import Path

    region_dir, site_dir = Path(region_dir), Path(site_dir)
    by_id = {s.id: s for s in sites}
    for name in boxes:
        ids = [i for i in name.split("+") if i in by_id]
        for f in sorted((region_dir / name).glob("*.parquet")):
            df = pd.read_parquet(f)
            for i in ids:
                w, s_, e, n = by_id[i].thermal_bbox()
                sub = df[(df["longitude"] >= w) & (df["longitude"] <= e)
                         & (df["latitude"] >= s_) & (df["latitude"] <= n)] if not df.empty else df
                out = site_dir / i / f.name
                out.parent.mkdir(parents=True, exist_ok=True)
                sub.to_parquet(out)
