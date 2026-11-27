import numpy as np
import pandas as pd

from orbitcu import copper as C


class _S:
    def __init__(self, id):
        self.id, self.capacity_kt = id, 100.0


def test_site_estimates_k_pit_uses_only_published_pairs():
    months = pd.period_range("2022-01", "2023-12", freq="M")
    em = pd.DataFrame({"site_id": "A", "period": months.astype(str), "E_kt_yr": 120.0,
                       "E_se_kt_yr": 1.0, "n_pixels": 100,
                       "available_at": months.to_timestamp(how="end") + pd.Timedelta(days=5)})
    rep = pd.DataFrame({"site_id": "A", "period_start": pd.to_datetime(["2022-01-01"]),
                        "period_end": pd.to_datetime(["2022-12-31"]), "cu_kt": [100.0],
                        "published_at": pd.to_datetime(["2023-03-15"], utc=True)})
    out = C.site_estimates(em, [_S("A")], rep, freq="Q")
    assert out["method"].eq("reported").all()
    early = out[out["period"].isin(["2022Q1", "2022Q4", "2023Q1"])]
    assert early["k_pit"].isna().all()                            # figure not public yet
    late = out[out["period"] == "2023Q2"].iloc[0]
    assert abs(late["k_pit"] - 1.2) < 0.01 and abs(late["cu_kt_pit"] - 25.0) < 0.3
