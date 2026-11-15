"""Detector metrics (eval/metrics.py): event counting is exact against brute force."""

import numpy as np
import pytest

from horus.eval import metrics


def _brute_events(icao: np.ndarray, minute: np.ndarray, s: np.ndarray, tau: float) -> int:
    n, prev_on = 0, False
    for i in range(len(s)):
        cont = i > 0 and icao[i] == icao[i - 1] and minute[i] - minute[i - 1] == 60
        on = s[i] >= tau
        if on and not (cont and prev_on):
            n += 1
        prev_on = on
    return n


def _day(rng: np.random.Generator, n: int = 3000) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    icao = np.sort(rng.integers(0, 40, n)).astype(str)
    minute = np.zeros(n, dtype=np.int64)
    for a in np.unique(icao):
        m = icao == a
        steps = rng.choice([60, 60, 60, 120, 600], m.sum())
        minute[m] = np.cumsum(steps)
    s = rng.gamma(1.5, 1.0, n)
    s[rng.random(n) < 0.05] = np.nan
    return icao, minute, s


def test_event_counts_match_brute_force_across_days() -> None:
    rng = np.random.default_rng(0)
    days = [_day(rng) for _ in range(3)]
    ctr = metrics.FalseAlarmCounter(keep=10**9)
    for icao, minute, s in days:
        ctr.add(icao, minute, s, seconds=3600.0)
    for tau in [0.5, 1.0, 2.0, 4.0]:
        want = sum(_brute_events(i, m, np.nan_to_num(s, nan=-np.inf), tau) for i, m, s in days)
        assert ctr.events(tau)[0] == want
    assert ctr.hours == pytest.approx(3.0)


def test_top_tail_is_exact_above_floor_and_guards_below() -> None:
    rng = np.random.default_rng(1)
    icao, minute, s = _day(rng, 5000)
    full, tail = metrics.FalseAlarmCounter(keep=10**9), metrics.FalseAlarmCounter(keep=300)
    full.add(icao, minute, s, 3600.0)
    tail.add(icao, minute, s, 3600.0)
    taus = np.array([tail.floor + 1e-9, 4.0, 6.0])
    assert np.array_equal(full.events(taus), tail.events(taus))
    with pytest.raises(ValueError):
        tail.events(tail.floor)


def test_threshold_for_rate_is_the_lowest_safe_tau() -> None:
    rng = np.random.default_rng(2)
    icao, minute, s = _day(rng, 5000)
    ctr = metrics.FalseAlarmCounter(keep=10**9)
    ctr.add(icao, minute, s, seconds=3600.0 * 1000)  # 1000 h: rate == event count
    tau = ctr.threshold_for(5.0)
    grid = np.unique(np.nan_to_num(s, nan=-1))
    above = grid[grid >= tau]
    assert (ctr.rate_per_1000h(above) <= 5.0).all()
    below = grid[grid < tau]
    assert ctr.rate_per_1000h(below[-1:])[0] > 5.0


def test_summary_metrics_on_separable_and_random_scores() -> None:
    y = np.r_[np.ones(50), np.zeros(200)]
    perfect = metrics.summary(y, np.r_[np.full(50, 2.0), np.full(200, 1.0)], 1.5, 0.5)
    assert perfect["pr_auc"] == 1.0 and perfect["recall_at_tau"] == 1.0
    assert perfect["precision_at_recall"] == 1.0 and perfect["neg_flagged_at_tau"] == 0.0
    rng = np.random.default_rng(4)
    rand = metrics.summary(y, rng.random(250), 0.5, 0.5)
    assert rand["prevalence"] == pytest.approx(0.2)
    assert abs(rand["roc_auc"] - 0.5) < 0.12
