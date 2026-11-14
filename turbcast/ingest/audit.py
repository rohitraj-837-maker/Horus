"""Audit the live recorder's poll log: did it run continuously for the window?"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

GAP_SLACK_S = 60.0


@dataclass(frozen=True)
class AuditResult:
    n_polls: int
    ok_fraction: float
    span_h: float
    max_gap_s: float
    worst_gap_allowed_s: float
    bad_gaps: int
    passed: bool
    reasons: tuple[str, ...]


def read_poll_log(out_dir: str | Path, source: str) -> pd.DataFrame:
    files = sorted((Path(out_dir) / "_polls" / f"source={source}").glob("*.csv"))
    if not files:
        return pd.DataFrame(
            columns=["poll_time", "ok", "n_rows", "credits_remaining", "interval_s"]
        )
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    return df.sort_values("poll_time").reset_index(drop=True)


def audit(polls: pd.DataFrame, hours: float, end: float, min_ok: float = 0.9) -> AuditResult:
    """A gap between polls is bad if it exceeds 2x the interval the recorder itself
    planned after the previous poll (adaptive pacing and logged backoffs both set
    that interval) plus a fixed slack."""
    w = polls[(polls["poll_time"] > end - hours * 3600) & (polls["poll_time"] <= end)]
    reasons = []
    if len(w) < 2:
        return AuditResult(len(w), 0.0, 0.0, float("inf"), 0.0, 0, False, ("fewer than 2 polls",))
    t = w["poll_time"].to_numpy(dtype=float)
    gaps = t[1:] - t[:-1]
    allowed = 2.0 * w["interval_s"].to_numpy(dtype=float)[:-1] + GAP_SLACK_S
    bad = gaps > allowed
    span_h = (t[-1] - t[0]) / 3600.0
    ok_fraction = float(w["ok"].mean())
    if span_h < hours * 0.98:
        reasons.append(f"span {span_h:.2f} h < {hours * 0.98:.2f} h")
    if bad.any():
        reasons.append(f"{int(bad.sum())} unplanned gaps (max {gaps.max():.0f} s)")
    if ok_fraction < min_ok:
        reasons.append(f"ok fraction {ok_fraction:.3f} < {min_ok}")
    i = int(gaps.argmax())
    return AuditResult(
        n_polls=len(w),
        ok_fraction=ok_fraction,
        span_h=span_h,
        max_gap_s=float(gaps.max()),
        worst_gap_allowed_s=float(allowed[i]),
        bad_gaps=int(bad.sum()),
        passed=not reasons,
        reasons=tuple(reasons),
    )
