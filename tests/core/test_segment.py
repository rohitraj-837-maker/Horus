"""Level-flight segmentation on synthetic tracks with known climb timing."""

import numpy as np

from horus.core.segment import SegmentParams, level_flight
from tests import synth

P = SegmentParams()


def two_level_legs(seed=0):
    tr = synth.level_track(3600)
    climb = synth.add_step_climb(tr, 1800, 2000, 1000)  # 1800..1920 s
    tr.vr = tr.vr + synth.band_noise(len(tr.t), 15, synth.AUTOPILOT_BAND_HZ, seed=seed)
    tr.vr, _ = synth.add_flicker(synth.quantise(tr.vr), 0.01, seed=seed)
    return tr, climb


def test_two_legs_found_and_climb_excluded():
    tr, (c0, c1) = two_level_legs()
    seg = level_flight(tr.alt, tr.vr, tr.on_ground, 1.0, P)
    assert len(seg.bounds) == 2
    (s1, e1), (s2, e2) = seg.bounds
    assert s1 >= 30 and e1 <= c0 - P.trim_s  # leg 1 inside [trim, climb - trim]
    assert s2 >= c1 + P.trim_s and e2 <= 3600 - P.trim_s
    near_climb = (tr.t >= c0 - P.trim_s) & (tr.t < c1 + P.trim_s)
    assert not (seg.seg_id[near_climb] >= 0).any()
    leg1, leg2 = c0, 3600 - c1
    assert (e1 - s1) / leg1 > 0.85 and (e2 - s2) / leg2 > 0.85
    assert 0.85 < seg.level_share < 1.0


def test_below_fl200_never_level():
    tr = synth.level_track(1800, alt_ft=19000)
    seg = level_flight(tr.alt, tr.vr, tr.on_ground, 1.0, P)
    assert len(seg.bounds) == 0 and seg.level_share == 0.0


def test_on_ground_never_level():
    tr = synth.level_track(1800)
    tr.on_ground[:] = True
    assert len(level_flight(tr.alt, tr.vr, tr.on_ground, 1.0, P).bounds) == 0


def test_steady_descent_not_level():
    tr = synth.level_track(1800)
    tr.vr[:] = -250.0  # slow descent: range stays < 300 ft per 60 s, but median > 200
    tr.alt = 35000 - 250.0 * tr.t / 60.0
    assert len(level_flight(tr.alt, tr.vr, tr.on_ground, 1.0, P).bounds) == 0


def test_sparse_20s_sampling_still_segments():
    tr, (c0, c1) = two_level_legs(seed=1)
    alt = synth.observe_every(tr.alt, 20)
    vr = synth.observe_every(tr.vr, 20)
    seg = level_flight(alt, vr, tr.on_ground, 1.0, P)
    assert len(seg.bounds) == 2
    near_climb = (tr.t >= c0 - P.trim_s) & (tr.t < c1 + P.trim_s)
    assert not (seg.seg_id[near_climb] >= 0).any()


def test_empty_input():
    seg = level_flight(np.array([]), np.array([]), np.array([], bool), 1.0, P)
    assert len(seg.bounds) == 0 and np.isnan(seg.level_share)


def test_trim_applies_at_data_edges_even_with_empty_grid_around():
    # A level track padded with unobserved grid points on both sides: segments must
    # still start trim_s after the first observation and end trim_s before the last.
    p = SegmentParams()
    n, pad = 600, 200
    alt = np.full(n + 2 * pad, np.nan)
    alt[pad : pad + n] = 35000.0
    vr = np.where(np.isfinite(alt), 0.0, np.nan)
    seg = level_flight(alt, vr, np.zeros(len(alt), bool), 1.0, p)
    assert seg.bounds.tolist() == [[pad + 30, pad + n - 30]]
    padless = level_flight(alt[pad:-pad], vr[pad:-pad], np.zeros(n, bool), 1.0, p)
    assert padless.bounds.tolist() == [[30, n - 30]]


def test_inside_data_spans_short_gaps_only():
    from horus.core.segment import inside_data

    x = np.full(20, np.nan)
    x[[2, 5, 15]] = 1.0
    got = inside_data(x, max_gap=4)
    assert got.tolist() == [i in {2, 3, 4, 5, 15} for i in range(20)]
