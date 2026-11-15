"""Leakage test (CLAUDE.md rule 4): fails if a test-split date is in any fitted artefact."""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from horus import config
from horus.eval import splits

CFG = config.load()


def test_split_is_time_ordered_and_disjoint() -> None:
    assert max(CFG.splits.train) < min(CFG.splits.test)
    assert not set(CFG.splits.train) & set(CFG.splits.test)


def test_real_models_dir_has_no_leakage() -> None:
    splits.check_models_dir(config.resolve("models"), CFG)


def test_json_artefact_with_a_test_date_is_caught(tmp_path: Path) -> None:
    (tmp_path / "ok.json").write_text(json.dumps({"train_dates": CFG.splits.train[:3]}))
    splits.check_models_dir(tmp_path, CFG)
    (tmp_path / "bad.json").write_text(
        json.dumps({"train_dates": [*CFG.splits.train[:3], CFG.splits.test[0]]})
    )
    with pytest.raises(splits.LeakageError, match="test dates"):
        splits.check_models_dir(tmp_path, CFG)


def test_parquet_artefact_with_a_test_date_is_caught(tmp_path: Path) -> None:
    t = pa.table({"x": [1.0]})
    meta = {b"train_dates": json.dumps([CFG.splits.test[-1]]).encode()}
    pq.write_table(t.replace_schema_metadata(meta), tmp_path / "norm.parquet")
    with pytest.raises(splits.LeakageError):
        splits.check_models_dir(tmp_path, CFG)


def test_artefact_without_dates_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "tau.json").write_text(json.dumps({"tau": 1.0}))
    with pytest.raises(splits.LeakageError, match="without train_dates"):
        splits.check_models_dir(tmp_path, CFG)
