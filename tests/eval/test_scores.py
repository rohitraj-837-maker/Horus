"""Per-minute scorer (eval/scores.py) on readsb-thinned synthetic tracks."""

import numpy as np

from horus import config
from horus.eval import scores
from tests import synth

CFG = config.load()


def _thinned(sigma: float, seed: int, n: int = 1800) -> tuple[np.ndarray, np.ndarray]:
    tr = synth.level_track(n)
    vr = synth.quantise(tr.vr + synth.band_noise(n, sigma, seed=seed))
    keep = synth.readsb_thin(tr.t, vr)
    return tr.t[keep].astype(np.int64) + 1_764_633_600, vr[keep]


def test_turbulent_aircraft_outscores_smooth_one_after_thinning_and_hold() -> None:
    t_s, v_s = _thinned(0.0, 1)  # smooth: thinned to 20 s heartbeats
    t_t, v_t = _thinned(150.0, 2)  # sigma 150 ft/min, quantised then thinned
    assert len(t_s) < 100 < len(t_t)  # the readsb rule really thins smooth air
    key = np.concatenate([np.zeros(len(t_s), int), np.ones(len(t_t), int)])
    t = np.concatenate([t_s, t_t])  # both aircraft share epoch seconds
    vr = np.concatenate([v_s, v_t])
    n = len(t)
    cols = {
        "lat": np.full(n, 40.0),
        "lon": np.full(n, -100.0),
        "baro_alt_ft": np.full(n, 35000.0),
        "vr_fpm": vr,
        "on_ground": np.zeros(n, bool),
    }
    out = scores.score_arrays(key, t, cols, CFG)
    for c in scores.SCORES:
        v = out[c].to_numpy(dtype=float)
        assert not np.isinf(v).any()
    full = out[out["n_s"] == 60]
    smooth, turb = full[full["key"] == 0], full[full["key"] == 1]
    assert len(smooth) >= 25 and len(turb) >= 25
    for c in scores.SCORES:
        assert np.nanmedian(turb[c]) > 3 * max(np.nanmedian(smooth[c]), 1e-6), c
    assert np.nanmax(smooth["b2"]) == 0.0  # held smooth air: no residual at all
    # Level segments trim 30 s at each end of the half-hour track.
    assert 0.8 < smooth["n_b2"].sum() / smooth["n_s"].sum() < 1.0


def test_plausibility_gate_in_load_tracks(tmp_path) -> None:
    from datetime import date

    import pandas as pd

    d = tmp_path / "date=2025-12-02"
    d.mkdir()
    t0 = 1_764_633_600
    pd.DataFrame(
        {
            "icao24": ["a"] * 4,
            "time": [t0, t0 + 1, t0 + 2, t0 + 3],
            "lat": [40.0] * 4,
            "lon": [-100.0] * 4,
            "baro_alt_ft": [35000.0, 35000.0, 121200.0, 35000.0],
            "vr_fpm": [64.0, 32640.0, 0.0, -9984.0],
            "on_ground": [False] * 4,
            "typecode": ["B738"] * 4,
            "msg_type": ["adsb_icao"] * 4,
        }
    ).to_parquet(d / "part-0.parquet")
    tab = scores.load_tracks(date(2025, 12, 2), CFG, root=tmp_path).to_pandas()
    assert tab["time"].tolist() == [t0, t0 + 1, t0 + 3]  # 121,200 ft row dropped
    assert tab["vr_fpm"].isna().tolist() == [False, True, False]  # field-max code -> missing
