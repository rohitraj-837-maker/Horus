import numpy as np
from scipy import signal

from tests import synth


def test_band_noise_has_exact_sigma_and_in_band_power():
    x = synth.band_noise(3600, 150.0, seed=3)
    assert x.std() == np.float64(150.0) or abs(x.std() - 150.0) < 1e-9
    f, p = signal.welch(x, fs=1.0, nperseg=512)
    lo, hi = synth.TURB_BAND_HZ
    in_band = p[(f >= lo * 0.8) & (f <= hi * 1.2)].sum() / p.sum()
    assert in_band > 0.95


def test_quantise_on_grid():
    q = synth.quantise(synth.band_noise(1000, 100.0))
    assert np.all(q % 64 == 0)


def test_flicker_is_isolated_single_steps():
    base = np.zeros(5000)
    v, idx = synth.add_flicker(base, rate=0.02, seed=1)
    assert len(idx) > 50
    assert np.all(np.abs(v[idx]) == 64)
    assert np.all(v[idx - 1] == 0) and np.all(v[idx + 1] == 0)


def test_step_climb_integrates_altitude():
    tr = synth.level_track(600)
    s, e = synth.add_step_climb(tr, 100, 2000, 1000)
    assert e - s == 120
    assert tr.alt[0] == 35000 and tr.alt[-1] == 37000
    assert np.all(tr.vr[(tr.t >= s) & (tr.t < e)] == 1000)
