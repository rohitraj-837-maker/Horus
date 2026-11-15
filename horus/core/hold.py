"""Zero-order-hold reconstruction of change-driven tracks. Pure, vectorised, no I/O.

The adsb.lol history archive (readsb traces) does not store every message. readsb
stores a new point when the vertical rate has moved >= 200 ft/min from the last
stored value (or altitude/speed/track change enough), and otherwise once per ~20 s
heartbeat. Between stored points the aircraft's values are therefore approximately
unchanged, so holding each value until the next point rebuilds the 1 s series to
within that dead band. Measured on 2025-12-02: 99.5% of 18-21 s heartbeat pairs
differ by < 200 ft/min (reports/data_notes.md).

Many tracks (several aircraft, or one aircraft with coverage gaps) are laid out on
one uniform grid, each continuous piece followed by `sep_len` empty samples. With
`sep_len` >= the widest centred rolling window, no window ever mixes two pieces, so
the rolling statistics in `core/` can run once over the whole layout.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class HoldLayout:
    src: NDArray[np.int64]  # per grid sample: source row held there, -1 = no data
    time: NDArray[np.float64]  # per grid sample: epoch s, NaN in separators
    piece: NDArray[np.int64]  # per grid sample: piece id (separators belong to the piece before)
    piece_key: NDArray[np.int64]  # per piece: the key (aircraft code) it came from

    @property
    def observed(self) -> NDArray[np.bool_]:
        return self.src >= 0

    def take(self, values: NDArray[np.generic], fill: float = np.nan) -> NDArray[np.float64]:
        """Held values on the grid (`fill` where there is no data)."""
        v = np.asarray(values, dtype=float)
        out = np.full(len(self.src), fill)
        ok = self.src >= 0
        out[ok] = v[self.src[ok]]
        return out


def hold_layout(
    key: NDArray[np.integer],
    time_s: NDArray[np.integer],
    max_gap_s: int,
    sep_len: int,
    step_s: int = 1,
) -> HoldLayout:
    """Lay out held tracks on a uniform `step_s` grid.

    `key` and `time_s` must be sorted by (key, time) with unique (key, time) pairs.
    A new piece starts at each key change or wherever consecutive points are more
    than `max_gap_s` apart (coverage loss: nothing is held across it).
    """
    k = np.asarray(key, dtype=np.int64)
    t = np.asarray(time_s, dtype=np.int64)
    n = len(t)
    if n == 0:
        e = np.empty(0, np.int64)
        return HoldLayout(e, np.empty(0), e, e)
    if step_s < 1 or sep_len < 0 or max_gap_s < step_s:
        raise ValueError("need step_s >= 1, sep_len >= 0, max_gap_s >= step_s")
    dk, dt = np.diff(k), np.diff(t)
    if np.any(dk < 0) or np.any((dk == 0) & (dt <= 0)):
        raise ValueError("input must be sorted by (key, time) with unique times per key")

    new = np.ones(n, dtype=bool)
    new[1:] = (dk != 0) | (dt > max_gap_s)
    starts = np.flatnonzero(new)
    ends = np.append(starts[1:], n)  # exclusive
    t0 = t[starts]
    lens = (t[ends - 1] - t0) // step_s + 1  # data samples per piece
    span = lens + sep_len
    offsets = np.concatenate([[0], np.cumsum(span)[:-1]])
    total = int(span.sum())

    piece_of_pt = np.cumsum(new) - 1
    pos = offsets[piece_of_pt] + (t - t0[piece_of_pt]) // step_s
    marks = np.full(total, -1, dtype=np.int64)
    marks[pos] = np.arange(n)  # several points in one bin: the last one wins

    piece = np.repeat(np.arange(len(starts), dtype=np.int64), span)
    within = np.arange(total, dtype=np.int64) - offsets[piece]
    is_data = within < lens[piece]

    # Forward fill inside each piece (pieces start with a mark, so fill never
    # crosses a separator once separators are masked back out).
    last = np.maximum.accumulate(np.where(marks >= 0, np.arange(total), -1))
    src = np.where(is_data & (last >= 0), marks[np.maximum(last, 0)], -1)
    time = np.where(is_data, (t0[piece] + within * step_s).astype(float), np.nan)
    return HoldLayout(src, time, piece, k[starts])
