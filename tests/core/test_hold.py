"""Zero-order-hold layout (core/hold.py): exact grid positions, no holding across gaps."""

import numpy as np
import pytest

from horus.core import hold


def test_single_piece_holds_until_next_point() -> None:
    t = np.array([100, 103, 105])
    lay = hold.hold_layout(np.zeros(3, int), t, max_gap_s=25, sep_len=2)
    assert lay.src.tolist() == [0, 0, 0, 1, 1, 2, -1, -1]
    assert lay.time[:6].tolist() == [100, 101, 102, 103, 104, 105]
    assert np.isnan(lay.time[6:]).all()
    vals = lay.take(np.array([1.0, 2.0, 3.0]))
    assert vals[:6].tolist() == [1, 1, 1, 2, 2, 3]
    assert np.isnan(vals[6:]).all()


def test_gap_longer_than_max_starts_new_piece_and_is_not_filled() -> None:
    t = np.array([0, 10, 50])  # 40 s gap > 25 s: coverage loss
    lay = hold.hold_layout(np.zeros(3, int), t, max_gap_s=25, sep_len=3)
    held = lay.take(np.array([7.0, 8.0, 9.0]))
    assert np.nansum(held == 8.0) == 1  # value at t=10 is not held into the gap
    assert len(lay.piece_key) == 2
    assert int(lay.observed.sum()) == 11 + 1


def test_gap_exactly_max_is_held() -> None:
    lay = hold.hold_layout(np.zeros(2, int), np.array([0, 25]), max_gap_s=25, sep_len=0)
    assert lay.src.tolist() == [0] * 25 + [1]


def test_keys_never_share_a_piece_and_separators_isolate_windows() -> None:
    key = np.array([1, 1, 2, 2])
    t = np.array([0, 1, 0, 1])  # same epoch seconds for both aircraft
    lay = hold.hold_layout(key, t, max_gap_s=25, sep_len=5)
    assert lay.piece_key.tolist() == [1, 2]
    assert lay.src.tolist() == [0, 1, -1, -1, -1, -1, -1, 2, 3, -1, -1, -1, -1, -1]


def test_unsorted_or_duplicate_input_is_rejected() -> None:
    with pytest.raises(ValueError):
        hold.hold_layout(np.zeros(2, int), np.array([5, 5]), 25, 2)
    with pytest.raises(ValueError):
        hold.hold_layout(np.array([2, 1]), np.array([0, 1]), 25, 2)


def test_empty() -> None:
    lay = hold.hold_layout(np.empty(0, int), np.empty(0, int), 25, 2)
    assert len(lay.src) == 0
