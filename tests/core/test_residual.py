"""Residual turbulence statistic on synthetic signals (SPEC §9): injected turbulence
of known sigma is recovered after 64 ft/min quantisation; step-climbs and
quantisation flicker alone do not look like turbulence."""

import numpy as np
import pytest

from horus.core import residual
from horus.core.residual import ResidualParams
from horus.core.segment import SegmentParams, level_flight, mask_outside
from tests import synth

P = ResidualParams()
N = 4 * 3600


def window_truth(x: np.ndarray) -> float:
    """sigma the residual pipeline measures on the *unquantised* signal (the trend
    and window remove some low-frequency power; that is the reference, not 'sigma')."""
    s = residual.residual_stats(x, 1.0, ResidualParams(sheppard=False, flicker_weight=1.0))
    return float(np.sqrt(np.nanmean(s.var_raw)))


@pytest.mark.parametrize("sigma", [40.0, 64.0, 100.0, 150.0])
def test_sheppard_recovers_sigma_after_quantisation(sigma):
    x = synth.band_noise(N, sigma, seed=int(sigma))
    q = synth.quantise(x)
    truth = window_truth(x)
    on = residual.residual_stats(q, 1.0, ResidualParams(flicker_weight=1.0))
    off = residual.residual_stats(q, 1.0, ResidualParams(sheppard=False, flicker_weight=1.0))
    s_on = np.sqrt(np.nanmean(on.var))
    s_off = np.sqrt(np.nanmean(off.var_raw))
    assert s_on == pytest.approx(truth, rel=0.03)
    assert abs(s_off - truth) > abs(s_on - truth)  # correction helps


def test_smooth_air_with_flicker_is_far_below_turbulence():
    smooth = synth.band_noise(N, 15, synth.AUTOPILOT_BAND_HZ, seed=1)
    smooth, _ = synth.add_flicker(synth.quantise(smooth), 0.02, seed=1)
    turb = synth.quantise(synth.band_noise(N, 150, seed=2))
    s_smooth = residual.residual_stats(smooth, 1.0, P).sigma
    s_turb = residual.residual_stats(turb, 1.0, P).sigma
    assert np.nanmedian(s_smooth) < 25
    assert np.nanpercentile(s_smooth, 95) < np.nanpercentile(s_turb, 5)


def test_flicker_detected_and_downweighted():
    v = np.array([0, 64, 0, 0, -64, 0, 64, 64, 0], dtype=float)
    assert np.flatnonzero(residual.flicker_mask(v)).tolist() == [1, 4]
    gaps = np.array([0, np.nan, 64, np.nan, 0], dtype=float)
    assert np.flatnonzero(residual.flicker_mask(gaps)).tolist() == [2]
    smooth, idx = synth.add_flicker(np.zeros(N), 0.05, seed=4)
    full = residual.residual_stats(smooth, 1.0, ResidualParams(sheppard=False, flicker_weight=1.0))
    half = residual.residual_stats(smooth, 1.0, ResidualParams(sheppard=False, flicker_weight=0.5))
    assert half.n_flicker == len(idx)
    assert np.nanmean(half.var_raw) < np.nanmean(full.var_raw)


def test_step_climb_alone_does_not_trigger():
    tr = synth.level_track(3600)
    synth.add_step_climb(tr, 1800, 2000, 1000)
    tr.vr = synth.quantise(tr.vr + synth.band_noise(len(tr.t), 15, synth.AUTOPILOT_BAND_HZ, seed=5))
    seg = level_flight(tr.alt, tr.vr, tr.on_ground, 1.0, SegmentParams())
    s = residual.residual_stats(mask_outside(tr.vr, seg.seg_id), 1.0, P)
    assert np.nanmax(s.sigma) < 40  # vs ~150 for moderate turbulence


def test_turbulence_detected_inside_segment_with_climb():
    tr = synth.level_track(3600)
    synth.add_step_climb(tr, 1800, 2000, 1000)
    patch = (tr.t >= 600) & (tr.t < 900)
    tr.vr[patch] += synth.band_noise(int(patch.sum()), 150, seed=6)
    tr.vr = synth.quantise(tr.vr)
    seg = level_flight(tr.alt, tr.vr, tr.on_ground, 1.0, SegmentParams())
    s = residual.residual_stats(mask_outside(tr.vr, seg.seg_id), 1.0, P).sigma
    inside = (tr.t >= 620) & (tr.t < 880)
    outside = (seg.seg_id >= 0) & ~((tr.t >= 560) & (tr.t < 940))
    assert np.nanmedian(s[inside]) > 100
    assert np.nanmax(s[outside]) < 40


def test_sparse_sampling_uses_wider_windows():
    x = synth.quantise(synth.band_noise(N, 150, seed=7))
    sparse = synth.observe_every(x, 20)
    s_default = residual.residual_stats(sparse, 1.0, P)  # 15 s window: < 6 obs
    assert np.isnan(s_default.var).all()
    wide = ResidualParams(w_trend_s=600, w_stat_s=300, min_samples_stat=6)
    s_wide = residual.residual_stats(sparse, 1.0, wide)
    assert np.isfinite(s_wide.var).mean() > 0.9
    # ~20 s samples alias the 0.1-0.4 Hz band; spread is still captured
    assert np.nanmedian(s_wide.sigma) == pytest.approx(150, rel=0.2)


def test_accel_ramp_and_b1_correction():
    t = np.arange(0, 100, 1.0)
    vr = 60.0 * t  # +60 ft/min every second = 60 fpm/s
    a, dt = residual.accel_g(t, vr)
    np.testing.assert_allclose(a[1:], 60.0 / residual.FPM_S_PER_G)
    assert np.isnan(a[0]) and np.all(dt[1:] == 1.0)
    q_noise, _ = synth.add_flicker(np.zeros(N), 0.05, seed=8)
    b0 = residual.accel_rms(np.arange(N, dtype=float), q_noise, 1.0, 15, 6)
    b1 = residual.accel_rms(np.arange(N, dtype=float), q_noise, 1.0, 15, 6, quant_step_fpm=64)
    assert np.nanmean(b1) < np.nanmean(b0)


def test_accel_max_dt_masks_differences_across_gaps() -> None:
    t = np.array([0.0, 1.0, 200.0, 201.0])
    vr = np.array([0.0, 64.0, 6400.0, 6464.0])
    a, dt = residual.accel_g(t, vr, max_dt_s=1.5)
    assert np.isnan(a[0]) and np.isnan(a[2]) and np.isnan(dt[2])
    assert a[1] == a[3] == 64.0 / residual.FPM_S_PER_G
    a_all, _ = residual.accel_g(t, vr)
    assert np.isfinite(a_all[2])  # without the guard the gap is differenced


def test_flicker_is_not_detected_across_pieces() -> None:
    vr = np.array([0.0, 0.0, 64.0] + [np.nan] * 50 + [0.0, 0.0, 0.0])
    assert residual.flicker_mask(vr)[2]  # without a gap limit: 0, 64, (gap) 0 = toggle
    assert not residual.flicker_mask(vr, max_gap=31).any()


def test_b1_is_exactly_zero_at_the_quantisation_floor() -> None:
    # Alternating +-1 step every second: mean square of accel equals the B1 noise floor
    # 2 D^2/12 only on average; a pure quantisation staircase gives ms <= floor -> 0.
    t = np.arange(4000, dtype=float)
    vr = np.where((np.arange(4000) // 7) % 2 == 0, 0.0, 64.0)
    b1 = residual.accel_rms(t, vr, 1.0, 15.0, 6, quant_step_fpm=64.0, max_dt_s=1.5)
    assert np.nanmin(b1) == 0.0
    assert (b1[np.isfinite(b1)] == 0.0).mean() > 0.5
