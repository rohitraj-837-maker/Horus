"""Audit the live recorder's poll log: did it run continuously for the window?

A gap is *unplanned* if it exceeds 2x the interval the recorder itself planned.
An unplanned gap is *excused* only when macOS's own power log (`pmset -g log`)
shows the host asleep for all of it except the allowed interval: the recorder
process survived, the laptop was suspended. Excused gaps are counted and
reported, never hidden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
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
    excused_gaps: int = 0
    slept_s: float = 0.0


def read_poll_log(out_dir: str | Path, source: str) -> pd.DataFrame:
    files = sorted((Path(out_dir) / "_polls" / f"source={source}").glob("*.csv"))
    if not files:
        return pd.DataFrame(
            columns=["poll_time", "ok", "n_rows", "credits_remaining", "interval_s"]
        )
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    return df.sort_values("poll_time").reset_index(drop=True)


# "2026-11-15 06:46:19 +0300 Sleep               \tEntering Sleep state due to ..."
# Category must be followed by 2+ spaces, so "Wake Requests" lines don't match.
_PMSET = re.compile(r"^(\S+ \S+ [+-]\d{4}) (Sleep|Wake|DarkWake)\s{2,}(.*)$")


def parse_pmset_sleeps(text: str) -> np.ndarray:
    """Sleep intervals [start, end) in epoch s from `pmset -g log` text.

    Starts at "Entering Sleep state"; ends at the next Wake or DarkWake event
    ("Entering DarkWake state" counts as awake: processes run in DarkWake)."""
    out: list[tuple[float, float]] = []
    asleep_at: float | None = None
    for line in text.splitlines():
        m = _PMSET.match(line)
        if not m:
            continue
        t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S %z").timestamp()
        cat, msg = m.group(2), m.group(3).strip()
        if cat == "Sleep" and msg.startswith("Entering Sleep state"):
            if asleep_at is None:
                asleep_at = t
        elif cat in ("Wake", "DarkWake") or msg.startswith("Entering DarkWake state"):
            if asleep_at is not None:
                out.append((asleep_at, t))
                asleep_at = None
    return np.array(out, dtype=float).reshape(-1, 2)


def _overlap(a: float, b: float, sleeps: np.ndarray) -> float:
    if len(sleeps) == 0:
        return 0.0
    lo = np.maximum(sleeps[:, 0], a)
    hi = np.minimum(sleeps[:, 1], b)
    return float(np.clip(hi - lo, 0, None).sum())


def audit(
    polls: pd.DataFrame,
    hours: float,
    end: float,
    min_ok: float = 0.9,
    sleeps: np.ndarray | None = None,
) -> AuditResult:
    """A gap between polls is bad if it exceeds 2x the interval the recorder itself
    planned after the previous poll (adaptive pacing and logged backoffs both set
    that interval) plus a fixed slack. With `sleeps` (host sleep intervals), a bad
    gap is excused when its awake part is within the allowed interval."""
    w = polls[(polls["poll_time"] > end - hours * 3600) & (polls["poll_time"] <= end)]
    reasons = []
    if len(w) < 2:
        return AuditResult(len(w), 0.0, 0.0, float("inf"), 0.0, 0, False, ("fewer than 2 polls",))
    t = w["poll_time"].to_numpy(dtype=float)
    gaps = t[1:] - t[:-1]
    allowed = 2.0 * w["interval_s"].to_numpy(dtype=float)[:-1] + GAP_SLACK_S
    bad = gaps > allowed
    excused = np.zeros_like(bad)
    slept = 0.0
    if sleeps is not None:
        for j in np.flatnonzero(bad):
            asleep = _overlap(t[j], t[j + 1], sleeps)
            if gaps[j] - asleep <= allowed[j]:
                excused[j] = True
                slept += asleep
        bad = bad & ~excused
    span_h = (t[-1] - t[0]) / 3600.0
    ok_fraction = float(w["ok"].mean())
    if span_h < hours * 0.98:
        reasons.append(f"span {span_h:.2f} h < {hours * 0.98:.2f} h")
    if bad.any():
        reasons.append(f"{int(bad.sum())} unplanned gaps (max {gaps[bad].max():.0f} s)")
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
        excused_gaps=int(excused.sum()),
        slept_s=slept,
    )


def host_sleeps() -> np.ndarray:
    """Host sleep intervals from macOS `pmset -g log` (empty elsewhere)."""
    import subprocess

    try:
        txt = subprocess.run(
            ["pmset", "-g", "log"], capture_output=True, text=True, timeout=60, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return np.empty((0, 2))
    return parse_pmset_sleeps(txt)


def process_alive(pid_file: str | Path) -> bool:
    import os

    try:
        os.kill(int(Path(pid_file).read_text().strip()), 0)
        return True
    except (OSError, ValueError):
        return False
