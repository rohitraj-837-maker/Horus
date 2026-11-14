"""Synthetic vertical-rate signals with known ground truth (SPEC §9).

Real signal processing, no mocks: turbulence is white noise passed through a
Butterworth band-pass and scaled to an exact standard deviation; ADS-B quantisation
rounds to the 64 ft/min step; step-climbs integrate a constant climb rate.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

STEP = 64.0
TURB_BAND_HZ = (0.1, 0.4)  # vertical-rate response band typical of turbulence at 1 Hz
AUTOPILOT_BAND_HZ = (0.005, 0.05)  # slow altitude-hold wander in smooth air


def band_noise(
    n: int, sigma: float, band: tuple[float, float] = TURB_BAND_HZ, fs: float = 1.0, seed: int = 0
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    sos = signal.butter(4, band, btype="band", fs=fs, output="sos")
    x = signal.sosfiltfilt(sos, rng.normal(size=n))
    return x * (sigma / x.std()) if sigma > 0 else np.zeros(n)


def quantise(vr: np.ndarray, step: float = STEP) -> np.ndarray:
    return step * np.round(np.asarray(vr) / step)


def add_flicker(
    vr_q: np.ndarray, rate: float, seed: int = 0, step: float = STEP
) -> tuple[np.ndarray, np.ndarray]:
    """Insert isolated single-sample +-step toggles where neighbours are equal."""
    rng = np.random.default_rng(seed)
    v = vr_q.copy()
    i = np.arange(1, len(v) - 1)
    flat = i[(v[i - 1] == v[i]) & (v[i + 1] == v[i])]
    pick = flat[rng.random(len(flat)) < rate]
    pick = pick[np.diff(np.concatenate([[-10], pick])) > 2]  # keep them isolated
    v[pick] += step * rng.choice([-1.0, 1.0], len(pick))
    return v, pick


@dataclass
class Track:
    t: np.ndarray
    alt: np.ndarray
    vr: np.ndarray
    on_ground: np.ndarray


def level_track(duration_s: int, alt_ft: float = 35000.0, dt: float = 1.0) -> Track:
    n = int(duration_s / dt)
    return Track(np.arange(n) * dt, np.full(n, alt_ft), np.zeros(n), np.zeros(n, bool))


def add_step_climb(
    tr: Track, start_s: float, delta_ft: float, rate_fpm: float
) -> tuple[float, float]:
    """Constant-rate climb from `start_s`; altitude integrates vr. Returns (start, end)."""
    dur = abs(delta_ft) / rate_fpm * 60.0
    end = start_s + dur
    m = (tr.t >= start_s) & (tr.t < end)
    tr.vr[m] += np.sign(delta_ft) * rate_fpm
    climbed = np.clip(tr.t - start_s, 0, dur) / 60.0 * rate_fpm * np.sign(delta_ft)
    tr.alt = tr.alt + climbed
    return start_s, end


def observe_every(x: np.ndarray, k: int) -> np.ndarray:
    """Keep every k-th sample, NaN elsewhere (sparse/irregular feeds on a 1 s grid)."""
    out = np.full(len(x), np.nan)
    out[::k] = x[::k]
    return out
