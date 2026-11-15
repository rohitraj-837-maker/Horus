"""Per-type normalisation of the turbulence statistic (SPEC §5.3). Pure, no I/O.

Each aircraft type (or its size class, or everything, when a type has fewer than
`n_min` fitting units) gets the distribution of sigma in smooth air. The index T
says how extreme a sigma is for that type:

    quantile_scale  T = sigma / q_p(smooth sigma)            (default)
    robust_z        T = (log sigma - median log) / (1.4826 MAD log)
    percentile      T = fraction of smooth sigma below sigma (mid-rank for ties)

quantile_scale is a robust z-score centred at 0. It exists because on the held
adsb.lol series smooth-air sigma is exactly 0 most of the time (the 200 ft/min
dead band), which makes the median and MAD of log sigma undefined, and the
percentile index saturates at 1 in the extreme tail where detection thresholds
live. robust_z raises DegenerateFit when its scale would be 0 or undefined.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.typing import NDArray

METHODS = ("quantile_scale", "robust_z", "percentile")
LEVELS = ("type", "size", "global")


class DegenerateFitError(ValueError):
    pass


@dataclass(frozen=True)
class NormParams:
    method: str = "quantile_scale"
    quantile: float = 0.99
    n_min: int = 200


def _fit_one(v: NDArray[np.float64], p: NormParams) -> tuple[float, float]:
    """(centre, scale) for one group's smooth sigma values."""
    if p.method == "quantile_scale":
        q = float(np.quantile(v, p.quantile))
        if not q > 0:
            raise DegenerateFitError(f"q{p.quantile:g} of smooth sigma is {q}")
        return 0.0, q
    if p.method == "robust_z":
        if np.any(v <= 0):
            raise DegenerateFitError(f"{np.mean(v <= 0):.1%} of smooth sigma is 0: log undefined")
        lv = np.log(v)
        m = float(np.median(lv))
        s = 1.4826 * float(np.median(np.abs(lv - m)))
        if not s > 0:
            raise DegenerateFitError("MAD of log sigma is 0")
        return m, s
    if p.method == "percentile":
        return 0.0, 1.0  # the index needs the sample itself, see `fit`
    raise ValueError(f"unknown method {p.method!r}")


@dataclass(frozen=True)
class TypeNorm:
    """Fitted table: one row per (level, key) with centre/scale, unit and value counts.
    For `percentile`, `samples` keeps each group's sorted smooth values."""

    params: NormParams
    table: pd.DataFrame
    samples: dict[tuple[str, str], NDArray[np.float64]]


def fit(
    sigma: NDArray[np.floating],
    typecode: NDArray[np.object_],
    size_class: NDArray[np.object_],
    unit: NDArray[np.generic],
    p: NormParams,
) -> TypeNorm:
    """Fit from smooth-air samples. `unit` identifies independent fitting units
    (e.g. aircraft-day); a group needs >= p.n_min distinct units."""
    df = pd.DataFrame(
        {
            "v": np.asarray(sigma, dtype=float),
            "type": pd.Series(typecode, dtype=object).fillna("UNK").to_numpy(),
            "size": pd.Series(size_class, dtype=object).fillna("UNK").to_numpy(),
            "unit": unit,
        }
    )
    df = df[np.isfinite(df["v"])]
    rows, samples = [], {}
    groups: list[tuple[str, str, pd.DataFrame]] = [("global", "ALL", df)]
    groups += [("size", str(k), g) for k, g in df.groupby("size", sort=True) if k != "UNK"]
    groups += [("type", str(k), g) for k, g in df.groupby("type", sort=True) if k != "UNK"]
    for level, key, g in groups:
        n_units = int(g["unit"].nunique())
        if level != "global" and n_units < p.n_min:
            continue
        v = g["v"].to_numpy()
        centre, scale = _fit_one(v, p)
        rows.append(
            {
                "level": level,
                "key": key,
                "centre": centre,
                "scale": scale,
                "n_units": n_units,
                "n_values": len(v),
                "zero_frac": float(np.mean(v == 0)),
            }
        )
        if p.method == "percentile":
            samples[(level, key)] = np.sort(v)
    return TypeNorm(p, pd.DataFrame(rows), samples)


def resolve(
    norm: TypeNorm, typecode: NDArray[np.object_], size_class: NDArray[np.object_]
) -> pd.DataFrame:
    """Per row: the (level, key) used, falling back type -> size class -> global."""
    t = norm.table
    have_type = set(t.loc[t["level"] == "type", "key"])
    have_size = set(t.loc[t["level"] == "size", "key"])
    tc = pd.Series(typecode, dtype=object).fillna("UNK")
    sc = pd.Series(size_class, dtype=object).fillna("UNK")
    level = np.where(tc.isin(have_type), "type", np.where(sc.isin(have_size), "size", "global"))
    key = np.where(level == "type", tc, np.where(level == "size", sc, "ALL"))
    return pd.DataFrame({"level": level, "key": key})


def index(
    norm: TypeNorm,
    sigma: NDArray[np.floating],
    typecode: NDArray[np.object_],
    size_class: NDArray[np.object_],
) -> NDArray[np.float64]:
    """Turbulence index T per row (NaN where sigma is NaN)."""
    s = np.asarray(sigma, dtype=float)
    which = resolve(norm, typecode, size_class)
    t = norm.table.set_index(["level", "key"])
    idx = pd.MultiIndex.from_frame(which)
    centre = t["centre"].reindex(idx).to_numpy()
    scale = t["scale"].reindex(idx).to_numpy()
    p = norm.params
    out = np.full(len(s), np.nan)
    if p.method == "quantile_scale":
        out = s / scale
    elif p.method == "robust_z":
        with np.errstate(divide="ignore"):
            out = (np.log(s) - centre) / scale
    else:
        for (lv, k), g in which.groupby(["level", "key"]).groups.items():
            ref = norm.samples[(str(lv), str(k))]
            r = s[np.asarray(g)]
            lo = np.searchsorted(ref, r, side="left")
            hi = np.searchsorted(ref, r, side="right")
            out[np.asarray(g)] = (lo + hi) / 2.0 / len(ref)
    return np.where(np.isfinite(s), out, np.nan)
