"""Nowcast bench helpers: ECE and pass building."""

import numpy as np
import pandas as pd

from horus import config
from horus.core import grid
from horus.eval import nowcast_bench as nb
from horus.eval import passes

CFG = config.load()


def test_ece_zero_when_calibrated_and_positive_when_not() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 200_000)
    y = rng.random(len(p)) < p
    assert nb.ece(y, p, 10) < 0.01
    assert nb.ece(y, np.clip(p * 0.5, 0, 1), 10) > 0.2


def test_passes_collapse_consecutive_minutes_in_one_cell() -> None:
    ids = grid.region_cells(38.0, 40.0, -101.0, -99.0, CFG.grid.h3_res)
    obs = pd.DataFrame(
        {
            "icao24": ["a", "a", "a", "a"],
            "minute": [0, 60, 120, 300],  # gap before the last minute
            "lat": [39.0, 39.0, 39.0, 39.0],
            "lon": [-100.0, -100.0, -100.0, -100.0],
            "alt_ft": [35000.0, 35000.0, 35000.0, 35000.0],
            "T": [0.1, 2.0, 0.3, 0.5],
        }
    )
    p = passes.build(obs, "T", CFG, ids)
    assert len(p) == 2
    assert p["T"].tolist() == [2.0, 0.5]
    assert p["t0"].tolist() == [0, 300] and p["t1"].tolist() == [180, 360]


def test_cell_ids_missing_position_is_outside_lattice() -> None:
    ids = grid.region_cells(38.0, 40.0, -101.0, -99.0, CFG.grid.h3_res)
    c = passes.cell_ids(np.array([39.0, np.nan]), np.array([-100.0, -100.0]), CFG.grid.h3_res)
    idx = grid.cell_index(c, ids)
    assert idx[0] >= 0 and idx[1] == -1


def test_reliability_plot_handles_constant_forecast(tmp_path) -> None:
    from horus.eval import plots

    y = np.r_[np.ones(10), np.zeros(990)].astype(bool)
    plots.reliability(y, np.full(1000, 0.01), "clim", tmp_path / "c.png")
    plots.reliability(y, np.linspace(0.001, 0.2, 1000), "n1", tmp_path / "n.png")
    assert (tmp_path / "c.png").stat().st_size > 0 and (tmp_path / "n.png").exists()
