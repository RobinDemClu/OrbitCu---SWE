import pandas as pd
import pytest

from orbitcu import so2_versions as sv


def test_version_of_orbit_and_stream():
    assert sv.version_of(22000, "OFFL") == "020201"
    assert sv.version_of(42023, "OFFL") == "020800"
    # NRTI switches a few days after OFFL: the same orbit is still old there
    assert sv.version_of(42050, "NRTI") == "020701"
    assert sv.version_of(42050, "OFFL") == "020800"


def test_factors_chain_and_rule():
    # gains whose interval contains 1 are not applied
    assert sv.applied_gain("020701") == 1.0 and sv.applied_gain("020700") == 1.0
    assert sv.factor_to_reference("020800") == 1.0
    assert sv.factor_to_reference("020701") == pytest.approx(1.185)
    f = sv.factor_to_reference("020201")
    assert f == pytest.approx(1.039 * 0.932 * 1.087 * 1.185)
    assert sv.factor_to_reference("030001", "020800") == 1.0  # 3.0 gain not significant


def test_correct_pixels_keeps_raw():
    px = pd.DataFrame({"orbit": [42000, 42100], "mode": ["OFFL", "OFFL"], "so2_1km": [1.0, 1.0]})
    c = sv.correct_pixels(px)
    assert list(c["version"]) == ["020701", "020800"]
    assert c["so2_1km"].round(3).tolist() == [1.185, 1.0] and c["so2_1km_raw"].tolist() == [1.0, 1.0]


def test_missing_orbit_left_uncorrected():
    assert sv.version_of(float("nan"), "OFFL") is None and sv.version_of(None, "NRTI") is None
    px = pd.DataFrame({"orbit": [float("nan"), 42000], "mode": ["OFFL", "OFFL"], "so2_1km": [1.0, 1.0]})
    c = sv.correct_pixels(px)
    assert c["version_factor"].tolist()[0] == 1.0 and c["so2_1km"].round(3).tolist() == [1.0, 1.185]
