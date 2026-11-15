"""Time-split leakage guard (CLAUDE.md rule 4, SPEC §9).

Every fitted artefact in models/ records the dates it was fitted on
(`train_dates` in JSON, or `train_dates` key-value metadata in Parquet). A test date
in any of them is leakage."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from horus.config import Config


class LeakageError(AssertionError):
    pass


def artefact_dates(path: Path) -> list[str] | None:
    """Fitted dates recorded in an artefact, or None if it records none."""
    if path.suffix == ".json":
        data = json.loads(path.read_text())
        dates = data.get("train_dates") if isinstance(data, dict) else None
        return [str(d) for d in dates] if dates is not None else None
    if path.suffix == ".parquet":
        meta = pq.read_schema(path).metadata or {}
        raw = meta.get(b"train_dates")
        return [str(d) for d in json.loads(raw)] if raw is not None else None
    return None


def check_dates(dates: list[str], cfg: Config, what: str = "artefact") -> None:
    leaked = sorted(set(dates) & set(cfg.splits.test))
    if leaked:
        raise LeakageError(
            f"{what}: fitted on test dates {leaked[:5]}{'...' if len(leaked) > 5 else ''}"
        )
    outside = sorted(set(dates) - set(cfg.splits.train))
    if outside:
        raise LeakageError(f"{what}: fitted on dates outside the train split {outside[:5]}")


def check_models_dir(models_dir: Path, cfg: Config) -> list[Path]:
    """Raise on any leaking artefact; also on fitted artefacts that record no dates.
    Returns the artefacts checked."""
    checked = []
    for p in sorted(models_dir.glob("*")):
        if p.suffix not in {".json", ".parquet"}:
            continue
        dates = artefact_dates(p)
        if dates is None:
            raise LeakageError(f"{p.name}: fitted artefact without train_dates")
        check_dates(dates, cfg, p.name)
        checked.append(p)
    return checked
