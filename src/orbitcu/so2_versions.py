"""Correction of the TROPOMI SO2 processor-version steps.

The Sentinel-5P SO2 product is made by a processor that is upgraded about once a year
(v2.2 to v2.8 over 2022-2026, v3.0 since November 2026). The archive is not reprocessed, so
every upgrade puts a step in the time series of every site. Year-on-year changes then mix
real emission changes with processor changes (the 2026 jumps at Norilsk and Sarcheshmeh).

Measurement: at each upgrade the near-real-time stream (NRTI) and the offline stream (OFFL)
switch a few days apart, so for a few days the *same orbits, same ground pixels* exist in
both the old and the new version. With

    pre  = OFFL/NRTI on orbits where both are old,
    diff = OFFL/NRTI on orbits where OFFL is new and NRTI old,
    post = OFFL/NRTI on orbits where both are new,

the version gain (new / old) is estimated twice, ``diff/pre`` (OFFL stream) and
``diff/post`` (NRTI stream); the gain is their geometric mean. The ratio is the sum of
columns over clearly enhanced pixels (mean of both versions > 1.5e-3 mol m-2, within 20 km
of a site; symmetric selection so there is no regression-to-the-mean bias), with a 90 %
interval from an orbit-cluster bootstrap. Data: 20 sites, 7-18 overlapping orbits per
upgrade.

Rule fixed in advance: a gain is applied only when its 90 % interval excludes 1; otherwise
1. The v2.7.0 upgrade (Dec 2024) cannot be measured this way (both streams switched on the
same day after a month without data); it is left at 1 and flagged.

Columns are put on the scale of the reference version (default v2.8.0) by multiplying by
the product of the gains between the pixel's version and the reference. Because the
emission fit has its own background term, only the multiplicative part matters for the
emission rate.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# version: (first OFFL orbit, first NRTI orbit, gain new/old, 90 % low, 90 % high)
VERSIONS = {
    "020201": (None, None, np.nan, np.nan, np.nan),
    "020300": (22768, 22813, 1.039, 1.001, 1.095),
    "020401": (24681, 24710, 0.932, 0.871, 0.981),
    "020500": (29818, 29879, 0.987, 0.963, 1.005),
    "020601": (31705, 31751, 1.087, 1.014, 1.157),
    "020700": (37196, 37196, np.nan, np.nan, np.nan),   # not measurable (no overlap)
    "020701": (38459, 38504, 1.118, 0.984, 1.224),
    "020800": (42023, 42080, 1.185, 1.148, 1.295),
    "030001": (46989, 47047, 1.032, 0.956, 1.139),
}
ORDER = list(VERSIONS)
REFERENCE = "020800"


def version_of(orbit, mode: str) -> str | None:
    """Processor version of an orbit in a stream (OFFL, NRTI; RPRO files of 2025 are v2.7.1).
    ``None`` when the orbit number is missing (the pixel is then left uncorrected)."""
    if mode == "RPRO":
        return "020701"
    try:
        orbit = float(orbit)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(orbit):
        return None
    col = 1 if mode == "NRTI" else 0
    v = ORDER[0]
    for k in ORDER[1:]:
        first = VERSIONS[k][col]
        if first is not None and orbit >= first:
            v = k
    return v


def applied_gain(version: str) -> float:
    g, lo, hi = VERSIONS[version][2:]
    if not np.isfinite(g) or (lo <= 1.0 <= hi):
        return 1.0
    return g


def factor_to_reference(version: str, reference: str = REFERENCE) -> float:
    """Multiply a column of ``version`` by this to put it on the ``reference`` scale."""
    i, j = ORDER.index(version), ORDER.index(reference)
    f = 1.0
    if i < j:
        for k in ORDER[i + 1:j + 1]:
            f *= applied_gain(k)
    elif i > j:
        for k in ORDER[j + 1:i + 1]:
            f /= applied_gain(k)
    return f


def correct_pixels(px: pd.DataFrame, column: str = "so2_1km", reference: str = REFERENCE) -> pd.DataFrame:
    """Add ``version`` and ``version_factor`` and rescale ``column`` in place of the raw one
    (the raw value is kept as ``<column>_raw``)."""
    px = px.copy()
    keys = px[["orbit", "mode"]].drop_duplicates()
    keys["version"] = [version_of(o, m) for o, m in zip(keys["orbit"], keys["mode"])]
    px = px.merge(keys, on=["orbit", "mode"], how="left")
    fac = {v: factor_to_reference(v, reference) for v in px["version"].dropna().unique()}
    px["version_factor"] = px["version"].map(fac).fillna(1.0)
    px[f"{column}_raw"] = px[column]
    px[column] = px[column] * px["version_factor"]
    return px
