"""Point-in-time (PIT) availability rules.

Every ingested record carries three timestamps (all UTC):

* ``acquired_at``  - when the satellite observed the scene;
* ``available_at`` - the earliest time a user could have downloaded the
  record in the form we store it. The backtest must only use records with
  ``available_at <= decision_time``;
* ``ingested_at``  - when *we* fetched it (audit only).

Where the provider publishes a processing / creation timestamp we use it
(``basis = "provider_timestamp"``). Otherwise we apply a documented,
deliberately conservative latency rule (``basis = "latency_rule"``).
Reprocessed archives (e.g. Sentinel-2 baseline 05.xx, FIRMS "SP") carry
timestamps that are *later* than the original near-real-time release or
contain slightly different data; see the project report, ``docs/latex/orbitcu_report.pdf`` for how each case
is handled.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Conservative latencies (upper bounds of the providers' stated timeliness).
LATENCY = {
    # NASA FIRMS: VIIRS/MODIS NRT is released within ~3 h of overpass.
    "firms_nrt": timedelta(hours=3),
    # Sentinel-5P TROPOMI: NRTI within 3 h, OFFL within ~12 h - 5 days.
    "s5p_nrti": timedelta(hours=3),
    "s5p_offl": timedelta(days=5),
    # ERA5T (preliminary ERA5) is published ~5 days behind real time.
    "era5t": timedelta(days=5),
    # Fallbacks when STAC metadata lack a creation time.
    "sentinel2_l2a": timedelta(days=1),
    "landsat_c2_l2": timedelta(days=16),
}


def utc(ts) -> datetime:
    """Parse an ISO string / datetime / pandas Timestamp into aware UTC."""
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def available_at(acquired_at, rule: str, provider_ts=None) -> tuple[datetime, str]:
    """Return ``(available_at, basis)`` for one record.

    ``provider_ts`` wins when given *and* not earlier than acquisition
    (a provider timestamp before acquisition would be a metadata error).
    """
    acq = utc(acquired_at)
    if provider_ts is not None:
        p = utc(provider_ts)
        if p >= acq:
            return p, "provider_timestamp"
    return acq + LATENCY[rule], "latency_rule"


def usable(df, decision_time) -> "object":
    """Filter a DataFrame with an ``available_at`` column to what was knowable."""
    return df[df["available_at"] <= utc(decision_time)]
