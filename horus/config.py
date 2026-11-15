"""Load config.yaml into frozen, typed dataclasses.

Missing or unknown keys raise immediately, so a typo in config.yaml can never
silently fall back to a default.
"""

from __future__ import annotations

import dataclasses
import typing
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO_ROOT / "config.yaml"


@dataclass(frozen=True)
class Region:
    name: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


@dataclass(frozen=True)
class Units:
    vr_step_fpm: float


@dataclass(frozen=True)
class Segment:
    min_alt_ft: float
    window_s: int
    max_alt_range_ft: float
    max_abs_median_vr_fpm: float
    trim_s: int
    min_window_samples: int


@dataclass(frozen=True)
class Residual:
    w_trend_s: int
    w_stat_s: int
    min_samples_stat: int
    sheppard: bool
    flicker_weight: float


@dataclass(frozen=True)
class SmoothExclusion:
    time_s: int
    dist_km: float
    alt_ft: float


@dataclass(frozen=True)
class Normalise:
    n_min_segments: int
    smooth_exclusion: SmoothExclusion
    method: str


@dataclass(frozen=True)
class Detect:
    tau_detect: float | None
    target_fa_per_1000h: float
    w_rate: dict[str, float]
    w_type_clip: list[float]
    event_merge_gap_s: int


@dataclass(frozen=True)
class Grid:
    h3_res: int
    fl_min: int
    fl_max: int
    fl_bin: int
    half_life_s: float
    half_life_bands_fl: list[list[int]]
    advect_interval_s: int
    advection: bool
    l_min: float
    l_max: float
    theta_display: float


@dataclass(frozen=True)
class Winds:
    levels_hpa: list[int]
    refresh_h: int


@dataclass(frozen=True)
class Sensitivity:
    window_s: list[int]
    dist_km: list[float]


@dataclass(frozen=True)
class Negatives:
    min_passes: int
    exclusion_time_s: int
    exclusion_dist_km: float
    window_s: int


@dataclass(frozen=True)
class Match:
    window_s: int
    dist_km: float
    alt_ft: float
    positive_min_intensity: str
    sensitivity: Sensitivity
    negatives: Negatives


@dataclass(frozen=True)
class Eval:
    rates_s: list[int]
    nowcast_horizon_s: int
    nowcast_step_s: int
    ece_bins: int


@dataclass(frozen=True)
class Hold:
    max_gap_s: int
    msg_types: list[str]


@dataclass(frozen=True)
class Bench:
    source: str
    scores_dir: str
    min_fl: int
    neg_per_day: int
    seed: int
    recall_for_precision: float
    event_resolution_s: int


@dataclass(frozen=True)
class Splits:
    """Inclusive ISO date ranges. Time-based only (SPEC §3): train before test."""

    train_start: str
    train_end: str
    test_start: str
    test_end: str

    def _days(self, a: str, b: str) -> list[str]:
        d0, d1 = date.fromisoformat(a), date.fromisoformat(b)
        return [str(d0 + timedelta(days=i)) for i in range((d1 - d0).days + 1)]

    @property
    def train(self) -> list[str]:
        return self._days(self.train_start, self.train_end)

    @property
    def test(self) -> list[str]:
        return self._days(self.test_start, self.test_end)

    def split_of(self, day: str) -> str | None:
        if self.train_start <= day <= self.train_end:
            return "train"
        if self.test_start <= day <= self.test_end:
            return "test"
        return None


@dataclass(frozen=True)
class Live:
    source: str
    poll_interval_s: int
    backoff_max_s: int
    flush_interval_s: int
    out_dir: str
    credentials_path: str
    opensky_bbox_credit_cost: int
    adsblol_radius_nm: float
    adsblol_request_spacing_s: float


@dataclass(frozen=True)
class Config:
    region: Region
    units: Units
    segment: Segment
    residual: Residual
    normalise: Normalise
    detect: Detect
    grid: Grid
    winds: Winds
    match: Match
    hold: Hold
    bench: Bench
    eval: Eval
    splits: Splits
    live: Live


class ConfigError(ValueError):
    pass


def _build(cls: type[Any], data: Any, path: str) -> Any:
    """Recursively build dataclass `cls` from a YAML mapping, rejecting bad keys."""
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping, got {type(data).__name__}")
    hints = typing.get_type_hints(cls)
    names = {f.name for f in dataclasses.fields(cls)}
    missing = names - data.keys()
    unknown = data.keys() - names
    if missing:
        raise ConfigError(f"{path}: missing keys {sorted(missing)}")
    if unknown:
        raise ConfigError(f"{path}: unknown keys {sorted(unknown)}")
    kwargs: dict[str, Any] = {}
    for name in names:
        hint = hints[name]
        value = data[name]
        if dataclasses.is_dataclass(hint):
            value = _build(typing.cast(type[Any], hint), value, f"{path}.{name}")
        elif hint is float:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ConfigError(f"{path}.{name}: expected a number, got {value!r}")
            value = float(value)
        elif hint is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"{path}.{name}: expected an integer, got {value!r}")
        elif hint is bool and not isinstance(value, bool):
            raise ConfigError(f"{path}.{name}: expected true/false, got {value!r}")
        kwargs[name] = value
    return cls(**kwargs)


def _check(cfg: Config) -> None:
    r = cfg.region
    if not (r.lat_min < r.lat_max and r.lon_min < r.lon_max):
        raise ConfigError("region: min must be < max")
    g = cfg.grid
    if (g.fl_max - g.fl_min) % g.fl_bin:
        raise ConfigError("grid: (fl_max - fl_min) must be a multiple of fl_bin")
    if not g.l_min < 0 < g.l_max:
        raise ConfigError("grid: need l_min < 0 < l_max")
    if not 0 < g.theta_display < 1:
        raise ConfigError("grid: theta_display must be in (0, 1)")
    if cfg.live.source not in {"opensky", "adsblol"}:
        raise ConfigError("live.source must be opensky or adsblol")
    if cfg.normalise.method not in {"robust_z", "percentile"}:
        raise ConfigError("normalise.method must be robust_z or percentile")
    sp = cfg.splits
    try:
        for d in (sp.train_start, sp.train_end, sp.test_start, sp.test_end):
            date.fromisoformat(d)
    except ValueError as e:
        raise ConfigError(f"splits: bad ISO date ({e})") from None
    if sp.train_start > sp.train_end or sp.test_start > sp.test_end:
        raise ConfigError("splits: each range needs start <= end")
    if sp.train_end >= sp.test_start:
        raise ConfigError("splits: train must end before test starts (no overlap, time order)")


def from_dict(raw: dict[str, Any]) -> Config:
    """Build and validate a Config from an already-parsed mapping."""
    # YAML reads bare dates as datetime.date; keep splits as ISO strings.
    splits = raw.get("splits")
    if isinstance(splits, dict):
        raw = {**raw, "splits": {k: str(v) for k, v in splits.items()}}
    cfg = typing.cast(Config, _build(Config, raw, "config"))
    _check(cfg)
    return cfg


def resolve(p: str | Path) -> Path:
    """Config paths are relative to the repo root, not the current directory."""
    p = Path(p)
    return p if p.is_absolute() else REPO_ROOT / p


def load(path: str | Path = DEFAULT_PATH) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return from_dict(raw)
