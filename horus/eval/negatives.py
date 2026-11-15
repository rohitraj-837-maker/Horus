"""No-PIREP windows (SPEC §6.1 negatives).

Candidates are drawn uniformly from aircraft-minute rows that have no PIREP of any
intensity within negatives.exclusion_time_s / exclusion_dist_km, so they follow the
traffic distribution, as PIREPs do. Each becomes a window with exactly the PIREP
window shape (centred on that aircraft's position, time and altitude). The bench
then keeps positives and negatives alike only if >= negatives.min_passes aircraft
pass through, so both classes are scored from comparable traffic.
Absence of a PIREP is weak evidence of smooth air (SPEC §6.1).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from horus.eval.match import MINUTE_S


def sample_negative_windows(
    obs: pd.DataFrame, clear: np.ndarray, n: int, rng: np.random.Generator
) -> pd.DataFrame:
    """`n` windows centred on random clear aircraft-minutes (fewer if not enough)."""
    idx = np.flatnonzero(np.asarray(clear, dtype=bool))
    if len(idx) == 0 or n <= 0:
        return pd.DataFrame(columns=["time", "lat", "lon", "alt_lo_ft", "alt_hi_ft"])
    pick = np.sort(rng.choice(idx, size=min(n, len(idx)), replace=False))
    o = obs.iloc[pick]
    alt = o["alt_ft"].to_numpy(float)
    return pd.DataFrame(
        {
            "time": o["minute"].to_numpy(np.int64) + MINUTE_S // 2,
            "lat": o["lat"].to_numpy(float),
            "lon": o["lon"].to_numpy(float),
            "alt_lo_ft": alt,
            "alt_hi_ft": alt,
        }
    )
