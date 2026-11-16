"""Report figures (matplotlib, static PNG for reports/figures)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

INK, MUTED, GRID, SERIES = "#1f2328", "#6e7781", "#d0d7de", "#2f6fdb"


def reliability(y: np.ndarray, p: np.ndarray, title: str, path: Path, bins: int = 10) -> None:
    """Observed frequency vs mean forecast in equal-count bins of the forecast
    (rare events: equal-width bins would put almost everything in the first bin).
    Log-log axes; the dashed diagonal is perfect calibration."""
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    # Most grid cells sit exactly at the prior: give the prior its own bin, then
    # equal-count bins over the forecasts above it (else the bins collapse).
    lo_p = float(p.min())
    above = p[p > lo_p]
    q = np.quantile(above, np.linspace(0, 1, bins)) if len(above) else np.array([])
    edges = np.unique(np.r_[lo_p, np.nextafter(lo_p, np.inf), q, np.nextafter(p.max(), np.inf)])
    b = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)
    n = np.bincount(b, minlength=len(edges) - 1)
    ok = n > 0
    mp = np.bincount(b, weights=p, minlength=len(edges) - 1)[ok] / n[ok]
    obs = np.bincount(b, weights=y, minlength=len(edges) - 1)[ok] / n[ok]
    fig, ax = plt.subplots(figsize=(4.6, 4.2), dpi=150)
    lo = max(min(mp.min(), obs[obs > 0].min() if (obs > 0).any() else mp.min()) / 2, 1e-5)
    ax.plot([lo, 1], [lo, 1], ls="--", lw=1, color=MUTED)
    ax.plot(
        mp,
        np.maximum(obs, lo),
        color=SERIES,
        lw=2,
        marker="o",
        ms=6,
        markeredgecolor="white",
        markeredgewidth=1.5,
    )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(lo, 1)
    ax.set_ylim(lo, 1)
    ax.set_xlabel("Forecast probability (bin mean)", color=INK)
    ax.set_ylabel("Observed frequency", color=INK)
    ax.set_title(title, color=INK, fontsize=10, loc="left")
    ax.grid(True, which="major", color=GRID, lw=0.6)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
