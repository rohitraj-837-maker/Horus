import copy
import dataclasses

import pytest
import yaml

from turbcast import config
from turbcast.config import ConfigError


@pytest.fixture
def raw() -> dict:
    with open(config.DEFAULT_PATH) as f:
        return yaml.safe_load(f)


def test_repo_config_loads_with_spec_values():
    cfg = config.load()
    # Spot-check values that SPEC.md fixes explicitly.
    assert cfg.segment.min_alt_ft == 20000.0
    assert cfg.segment.window_s == 120
    assert cfg.segment.max_alt_range_ft == 300.0
    assert cfg.segment.max_abs_median_vr_fpm == 200.0
    assert cfg.segment.trim_s == 30
    assert cfg.residual.w_trend_s == 31
    assert cfg.residual.w_stat_s == 15
    assert cfg.units.vr_step_fpm == 64.0
    assert cfg.normalise.n_min_segments == 200
    assert cfg.grid.h3_res == 4
    assert cfg.grid.fl_bin == 20
    assert cfg.grid.half_life_s == 720
    assert cfg.grid.advect_interval_s == 60
    assert (cfg.match.window_s, cfg.match.dist_km, cfg.match.alt_ft) == (600, 40.0, 2000.0)
    assert cfg.eval.rates_s == [1, 5, 10]


def test_config_is_frozen():
    cfg = config.load()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.segment.window_s = 60  # type: ignore[misc]


def test_int_coerced_to_float(raw):
    cfg = config.from_dict(raw)
    assert isinstance(cfg.region.lat_min, float)
    assert isinstance(cfg.grid.half_life_s, float)


@pytest.mark.parametrize("section", ["segment", "grid", "match"])
def test_missing_key_raises(raw, section):
    bad = copy.deepcopy(raw)
    bad[section].pop(next(iter(bad[section])))
    with pytest.raises(ConfigError, match="missing keys"):
        config.from_dict(bad)


def test_unknown_key_raises(raw):
    bad = copy.deepcopy(raw)
    bad["segment"]["window_sec"] = 120  # typo of window_s
    with pytest.raises(ConfigError, match="unknown keys"):
        config.from_dict(bad)


def test_nested_unknown_key_reports_path(raw):
    bad = copy.deepcopy(raw)
    bad["match"]["negatives"]["typo"] = 1
    with pytest.raises(ConfigError, match=r"config\.match\.negatives"):
        config.from_dict(bad)


@pytest.mark.parametrize(
    "section,key,value",
    [("segment", "window_s", "120"), ("segment", "window_s", 1.5), ("grid", "advection", "yes")],
)
def test_wrong_type_raises(raw, section, key, value):
    bad = copy.deepcopy(raw)
    bad[section][key] = value
    with pytest.raises(ConfigError):
        config.from_dict(bad)


def test_overlapping_splits_raise(raw):
    bad = copy.deepcopy(raw)
    bad["splits"] = {"train": ["2026-12-01", "2026-12-02"], "test": ["2026-12-02"]}
    with pytest.raises(ConfigError, match="both train and test"):
        config.from_dict(bad)


def test_train_after_test_raises(raw):
    bad = copy.deepcopy(raw)
    bad["splits"] = {"train": ["2026-12-10"], "test": ["2026-12-01"]}
    with pytest.raises(ConfigError, match="earlier"):
        config.from_dict(bad)


def test_yaml_dates_become_iso_strings(tmp_path, raw):
    raw["splits"] = {"train": [], "test": []}
    text = (
        yaml.safe_dump(raw)
        .replace("train: []", "train:\n  - 2026-12-01")
        .replace("test: []", "test:\n  - 2027-01-10")
    )
    p = tmp_path / "c.yaml"
    p.write_text(text)
    cfg = config.load(p)
    assert cfg.splits.train == ["2026-12-01"]
    assert cfg.splits.test == ["2027-01-10"]


def test_bad_fl_bin_raises(raw):
    bad = copy.deepcopy(raw)
    bad["grid"]["fl_bin"] = 30  # 260 not divisible by 30
    with pytest.raises(ConfigError, match="multiple of fl_bin"):
        config.from_dict(bad)
