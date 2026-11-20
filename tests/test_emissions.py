import numpy as np
import pandas as pd

from orbitcu import emissions as em


def test_kernel_integrates_to_one():
    cfg = em.FitConfig()
    x = np.arange(-100, 100, 0.5)
    y = np.arange(-100, 1200, 0.5)
    X, Y = np.meshgrid(x, y)
    k = em.plume_kernel(X, Y, np.full(X.shape, 5.0), cfg)
    assert abs(k.sum() * 0.25 - 1) < 0.01
    # the plume lies downwind: more mass at y > 0 than at y < 0
    assert k[Y > 0].sum() > 5 * k[Y < 0].sum()


def test_rotation_puts_downwind_on_positive_y():
    # wind blowing to the east: a point 10 km east is 10 km downwind
    xr, yr = em.rotate(np.array([10.0]), np.array([0.0]), np.array([3.0]), np.array([0.0]))
    assert abs(yr[0] - 10) < 1e-9 and abs(xr[0]) < 1e-9


def test_fit_recovers_synthetic_emission():
    cfg = em.FitConfig()
    rng = np.random.default_rng(1)
    n_orb, n_px = 40, 400
    rows = []
    a_true = 2.0e5  # mol
    for o in range(n_orb):
        th = rng.uniform(0, 2 * np.pi)
        s = rng.uniform(2, 8)
        r = np.sqrt(rng.uniform(0, 1, n_px)) * cfg.radius_km
        phi = rng.uniform(0, 2 * np.pi, n_px)
        x, y = r * np.cos(phi), r * np.sin(phi)
        u, v = s * np.cos(th), s * np.sin(th)
        xr, yr = em.rotate(x, y, np.full(n_px, u), np.full(n_px, v))
        k = em.plume_kernel(xr, yr, np.full(n_px, s), cfg)
        vcd = a_true * k + 20 + rng.normal(0, 50, n_px)
        rows.append(pd.DataFrame({"item_id": o, "kernel": k, "vcd_mol_km2": vcd, "wind": s, "x_km": x, "y_km": y}))
    px = pd.concat(rows, ignore_index=True)
    res = em.fit_window(px, cfg, n_boot=50)
    assert abs(res["a_mol"] / a_true - 1) < 0.1
    e_true = a_true * em.M_SO2 / (cfg.tau_h * 3600) * em.SECONDS_PER_YEAR / 1e6
    assert abs(res["E_kt_yr"] / e_true - 1) < 0.1
    assert res["t_stat"] > 5
