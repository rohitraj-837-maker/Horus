"""Nowcast grid (core/grid.py): decay, evidence, clamp, persistence, lattice."""

import numpy as np
import pytest

from horus.core import grid

HL = np.array([720.0, 720.0, 1440.0])


def _p(**kw: float) -> grid.GridParams:
    return grid.GridParams(l0=-3.0, half_life_s=HL, **kw)


def test_decay_halves_excess_after_one_half_life_and_quarters_after_two() -> None:
    L = np.array([[1.0, 1.0, 1.0]])
    t = np.zeros((1, 3))
    one = grid.decayed(L, t, 720.0, -3.0, HL[None, :])
    two = grid.decayed(L, t, 1440.0, -3.0, HL[None, :])
    np.testing.assert_allclose(one[0, :2] + 3.0, 2.0, rtol=1e-6)
    np.testing.assert_allclose(two[0, :2] + 3.0, 1.0, rtol=1e-6)
    np.testing.assert_allclose(one[0, 2] + 3.0, 4.0 / np.sqrt(2), rtol=1e-6)  # longer band
    assert grid.decayed(L, t, -5.0, -3.0, HL[None, :])[0, 0] == 1.0  # never decays backwards


def test_lazy_read_equals_touching_the_cell() -> None:
    p = _p(a=2.0, tau=1.0)
    s = grid.GridState.empty(4, 3, p.l0, 0.0)
    grid.update(s, np.array([1]), np.array([0]), np.array([2.0]), np.array([1.0]), 100.0, p)
    lazy = grid.read(s, 820.0, p)[1, 0]
    grid.update(s, np.array([1]), np.array([0]), np.array([0.0]), np.array([1.0]), 820.0, p)
    assert grid.sigmoid(s.L[1, 0]) == pytest.approx(lazy, rel=1e-6)


def test_positive_evidence_sums_duplicates_and_is_clamped() -> None:
    p = _p(a=1.0, tau=1.0, l_pos_max=4.0, l_max=2.0)
    s = grid.GridState.empty(2, 3, p.l0, 0.0)
    c, f = np.array([0, 0, 1]), np.array([2, 2, 2])
    grid.update(s, c, f, np.array([2.0, 2.5, 0.5]), np.ones(3), 0.0, p)
    assert s.L[0, 2] == pytest.approx(-3.0 + 1.0 + 1.5)
    assert s.L[1, 2] == pytest.approx(-3.0)  # T below tau adds nothing
    grid.update(s, c, f, np.array([9.0, 9.0, 9.0]), np.ones(3), 0.0, p)
    assert s.L.max() == pytest.approx(2.0)  # clamp at l_max


def test_out_of_lattice_observations_are_ignored() -> None:
    p = _p()
    s = grid.GridState.empty(2, 3, p.l0, 0.0)
    grid.update(s, np.array([-1, 0]), np.array([0, -1]), np.array([9.0, 9.0]), np.ones(2), 0.0, p)
    assert (s.L == np.float32(p.l0)).all()


def test_persistence_never_decays_and_keeps_last_verdict() -> None:
    s = grid.GridState.empty(2, 1, -3.0, 0.0)
    grid.update_persistence(
        s, np.array([0, 1]), np.array([0, 0]), np.array([True, False]), 0.0, -3.0, 1.0
    )
    assert s.L[:, 0].tolist() == [1.0, -3.0]
    grid.update_persistence(s, np.array([0]), np.array([0]), np.array([False]), 9e9, -3.0, 1.0)
    assert s.L[0, 0] == -3.0


def test_region_lattice_and_index() -> None:
    lat = grid.region_cells(38.0, 40.0, -100.0, -97.0, 4)
    assert 30 < len(lat) < 80 and np.all(np.diff(lat) > 0)
    import h3

    inside = h3.str_to_int(h3.latlng_to_cell(39.0, -98.5, 4))
    outside = h3.str_to_int(h3.latlng_to_cell(10.0, 10.0, 4))
    assert grid.cell_index(np.array([inside, outside]), lat).tolist()[1] == -1
    assert grid.cell_index(np.array([inside]), lat)[0] >= 0


def _lattice() -> tuple[np.ndarray, grid.Lattice]:
    ids = grid.region_cells(30.0, 46.0, -110.0, -80.0, 4)
    return ids, grid.Lattice.build(ids)


def _centroid(state: grid.GridState, lat: grid.Lattice, l0: float) -> tuple[float, float]:
    w = np.maximum(state.L[:, 0].astype(float) - l0, 0.0)
    return float((w * lat.lat).sum() / w.sum()), float((w * lat.lon).sum() / w.sum())


def test_uniform_eastward_wind_moves_blob_expected_distance() -> None:
    from horus.core.geo import haversine_km

    ids, lat = _lattice()
    p = grid.GridParams(l0=-3.0, half_life_s=np.array([1e12]), l_min=-6, l_max=6)
    s = grid.GridState.empty(len(ids), 1, p.l0, 0.0)
    k = int(np.argmin(np.hypot(lat.lat - 38.0, lat.lon + 100.0)))
    s.L[k, 0] = 3.0
    for j in lat.nbr[k]:
        if j >= 0:
            s.L[j, 0] = 1.0
    c0 = _centroid(s, lat, p.l0)
    peak0 = float(s.L.max())
    u = np.full((len(ids), 1), 50.0)
    v = np.zeros((len(ids), 1))
    for step in range(60):
        grid.advect(s, lat, u, v, 60.0, 60.0 * (step + 1), p)
    c1 = _centroid(s, lat, p.l0)
    moved = haversine_km(c0[0], c0[1], c1[0], c1[1])
    assert moved == pytest.approx(180.0, rel=0.15)  # 50 m/s for 60 min
    assert c1[1] > c0[1]  # eastward
    assert float(s.L.max()) <= peak0 + 1e-5  # no mass created


def test_zero_wind_is_identity_and_locked_cells_stay() -> None:
    ids, lat = _lattice()
    p = grid.GridParams(l0=-3.0, half_life_s=np.array([1e12]))
    s = grid.GridState.empty(len(ids), 1, p.l0, 0.0)
    rng = np.random.default_rng(0)
    s.L[:, 0] = rng.uniform(-3, 2, len(ids)).astype(np.float32)
    before = s.L.copy()
    grid.advect(s, lat, np.zeros((len(ids), 1)), np.zeros((len(ids), 1)), 60.0, 0.0, p)
    np.testing.assert_allclose(s.L, before, atol=1e-5)
    locked = np.zeros(len(ids), bool)
    locked[:10] = True
    grid.advect(s, lat, np.full((len(ids), 1), 80.0), np.zeros((len(ids), 1)), 60.0, 0.0, p, locked)
    np.testing.assert_allclose(s.L[:10], before[:10], atol=1e-5)


def test_two_smooth_passes_drop_an_alert_one_does_not_and_l_min_holds() -> None:
    l0, l_hit, l_disp = -5.0, -1.0, -3.0
    p = grid.GridParams(
        l0=l0,
        half_life_s=np.array([1e12]),
        a=1e6,
        l_pos_max=l_hit - l0,
        tau=1.0,
        l_neg=(l_hit - l_disp) / 2 + 1e-3,
        l_min=-6.0,
    )
    s = grid.GridState.empty(1, 1, l0, 0.0)
    one, zero = np.array([0]), np.array([0])
    grid.update(s, one, zero, np.array([5.0]), np.ones(1), 0.0, p)
    assert s.L[0, 0] == pytest.approx(l_hit)
    smooth = np.array([True])
    grid.update(s, one, zero, np.array([0.0]), np.ones(1), 1.0, p, smooth)
    assert s.L[0, 0] > l_disp  # one smooth pass is not enough
    grid.update(s, one, zero, np.array([0.0]), np.ones(1), 2.0, p, smooth)
    assert s.L[0, 0] < l_disp
    for k in range(10):
        grid.update(s, one, zero, np.array([0.0]), np.ones(1), 3.0 + k, p, smooth)
    assert s.L[0, 0] == pytest.approx(-6.0)  # clamped, never deleted below l_min
