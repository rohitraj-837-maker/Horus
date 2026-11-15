"""Per-type normalisation (core/normalise.py) on synthetic smooth-air populations."""

import numpy as np
import pytest

from horus.core import normalise as nz


def _pop(rng: np.random.Generator, n_units: int, scale: float, zero_frac: float):
    v = rng.gamma(2.0, scale, n_units * 20)
    v[rng.random(len(v)) < zero_frac] = 0.0
    return v, np.repeat(np.arange(n_units), 20)


def _two_types(zero_frac: float = 0.0):
    rng = np.random.default_rng(0)
    va, ua = _pop(rng, 300, 10.0, zero_frac)  # light type: twice as bumpy in smooth air
    vb, ub = _pop(rng, 300, 5.0, zero_frac)
    v = np.r_[va, vb]
    tc = np.array(["A320"] * len(va) + ["B77W"] * len(vb), dtype=object)
    sc = np.array(["medium_jet"] * len(va) + ["heavy"] * len(vb), dtype=object)
    unit = np.r_[ua, ub + 1000]
    return v, tc, sc, unit


@pytest.mark.parametrize("method", ["quantile_scale", "robust_z", "percentile"])
def test_types_with_different_smooth_levels_get_the_same_index_distribution(method: str) -> None:
    v, tc, sc, unit = _two_types()
    norm = nz.fit(v, tc, sc, unit, nz.NormParams(method=method, n_min=200))
    t = nz.index(norm, v, tc, sc)
    a, b = t[tc == "A320"], t[tc == "B77W"]
    for q in (0.5, 0.9, 0.99):
        assert np.quantile(a, q) == pytest.approx(np.quantile(b, q), rel=0.08, abs=0.02)
    raw_ratio = np.quantile(v[tc == "A320"], 0.9) / np.quantile(v[tc == "B77W"], 0.9)
    assert raw_ratio > 1.8  # without normalisation the types differ ~2x


def test_fallback_type_then_size_then_global() -> None:
    v, tc, sc, unit = _two_types()
    norm = nz.fit(v, tc, sc, unit, nz.NormParams(n_min=200))
    which = nz.resolve(
        norm,
        np.array(["A320", "A21N", "XXXX", None], dtype=object),
        np.array(["medium_jet", "medium_jet", "light", None], dtype=object),
    )
    assert which["level"].tolist() == ["type", "size", "global", "global"]
    assert which["key"].tolist() == ["A320", "medium_jet", "ALL", "ALL"]


def test_small_groups_are_not_fitted() -> None:
    v, tc, sc, unit = _two_types()
    norm = nz.fit(v, tc, sc, unit, nz.NormParams(n_min=301))
    assert norm.table["level"].tolist() == ["global"]


def test_zero_heavy_smooth_air_breaks_robust_z_but_not_quantile_scale() -> None:
    v, tc, sc, unit = _two_types(zero_frac=0.7)  # like the held adsb.lol series
    with pytest.raises(nz.DegenerateFitError):
        nz.fit(v, tc, sc, unit, nz.NormParams(method="robust_z"))
    norm = nz.fit(v, tc, sc, unit, nz.NormParams(method="quantile_scale"))
    t = nz.index(norm, v, tc, sc)
    assert np.quantile(t[tc == "A320"], 0.99) == pytest.approx(1.0, rel=0.05)
    assert np.quantile(t[tc == "B77W"], 0.99) == pytest.approx(1.0, rel=0.05)
    assert np.isnan(nz.index(norm, np.array([np.nan]), tc[:1], sc[:1])).all()


def test_percentile_saturates_in_the_tail() -> None:
    v, tc, sc, unit = _two_types()
    norm = nz.fit(v, tc, sc, unit, nz.NormParams(method="percentile"))
    t = nz.index(norm, np.array([1e3, 1e6]), tc[:2], sc[:2])
    assert t[0] == t[1] == 1.0  # cannot rank beyond the smooth sample: why B3 uses scale
