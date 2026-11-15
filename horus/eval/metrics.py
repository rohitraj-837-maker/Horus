"""Detector metrics (SPEC §6.2): PR-AUC, recall at a false-alarm rate, precision at
a fixed recall.

False alarms are counted on aircraft-minute rows with no PIREP nearby. An alarm
event is a run of consecutive alarmed minutes on one aircraft, so for threshold tau

    events(tau) = #{rows: s >= tau} - #{rows: min(s, s_prev) >= tau}

where s_prev is the same aircraft's previous-minute score (-inf if that minute is
missing or not clear). Only the top tail of s and min(s, s_prev) is needed for the
low rates of interest, so each day keeps its `keep` largest values of both.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score


def run_minimums(
    icao: np.ndarray, minute: np.ndarray, s: np.ndarray, step_s: int = 60
) -> np.ndarray:
    """min(s, s_prev) per row (rows sorted by aircraft, minute); -inf with no prev."""
    s = np.nan_to_num(np.asarray(s, dtype=float), nan=-np.inf)
    prev = np.full(len(s), -np.inf)
    if len(s) > 1:
        same = (icao[1:] == icao[:-1]) & (minute[1:] - minute[:-1] == step_s)
        prev[1:] = np.where(same, s[:-1], -np.inf)
    out: np.ndarray = np.minimum(s, prev)
    return out


@dataclass
class FalseAlarmCounter:
    keep: int = 50_000
    hours: float = 0.0
    _s: list[np.ndarray] = field(default_factory=list)
    _m: list[np.ndarray] = field(default_factory=list)
    floor: float = -np.inf  # counts are exact only for tau > floor

    def add(self, icao: np.ndarray, minute: np.ndarray, s: np.ndarray, seconds: float) -> None:
        """One day's clear rows, sorted by (aircraft, minute)."""
        s = np.nan_to_num(np.asarray(s, dtype=float), nan=-np.inf)
        m = run_minimums(icao, minute, s)
        for arr, store in ((s, self._s), (m, self._m)):
            if len(arr) > self.keep:
                part = np.partition(arr, len(arr) - self.keep)
                self.floor = max(self.floor, float(part[len(arr) - self.keep]))
                arr = part[len(arr) - self.keep :]
            store.append(np.sort(arr))
        self.hours += seconds / 3600.0

    def events(self, tau: np.ndarray | float) -> np.ndarray:
        tau = np.atleast_1d(np.asarray(tau, dtype=float))
        if np.any(tau <= self.floor):
            raise ValueError(f"tau below the exact range (floor {self.floor:.4g}); raise `keep`")
        n = np.zeros(len(tau))
        for s, m in zip(self._s, self._m, strict=True):
            n += (len(s) - np.searchsorted(s, tau, side="left")) - (
                len(m) - np.searchsorted(m, tau, side="left")
            )
        return n

    def rate_per_1000h(self, tau: np.ndarray | float) -> np.ndarray:
        return self.events(tau) / self.hours * 1000.0

    def threshold_for(self, rate_per_1000h: float) -> float:
        """Smallest tau such that every tau' >= tau stays at or below the rate."""
        cand = np.unique(np.concatenate(self._s))
        cand = cand[np.isfinite(cand) & (cand > self.floor)]
        if len(cand) == 0:
            return float("inf")
        r = self.rate_per_1000h(cand)
        bad = np.flatnonzero(r > rate_per_1000h)
        if len(bad) == 0:
            if self.floor > -np.inf:
                raise ValueError("rate met at the lowest kept score; raise `keep`")
            return float(cand[0])
        if bad[-1] + 1 >= len(cand):
            return float(np.nextafter(cand[-1], np.inf))
        return float(cand[bad[-1] + 1])


def precision_at_recall(y: np.ndarray, s: np.ndarray, recall: float) -> float:
    p, r, _ = precision_recall_curve(y, s)
    ok = r >= recall
    return float(p[ok].max()) if ok.any() else float("nan")


def summary(y: np.ndarray, s: np.ndarray, tau: float, recall_target: float) -> dict[str, float]:
    y = np.asarray(y, dtype=int)
    s = np.asarray(s, dtype=float)
    pos = y == 1
    return {
        "n_pos": float(pos.sum()),
        "n_neg": float((~pos).sum()),
        "prevalence": float(pos.mean()),
        "pr_auc": float(average_precision_score(y, s)),
        "roc_auc": float(roc_auc_score(y, s)),
        "recall_at_tau": float((s[pos] >= tau).mean()),
        "neg_flagged_at_tau": float((s[~pos] >= tau).mean()),
        "precision_at_recall": precision_at_recall(y, s, recall_target),
    }


def frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)
