"""Aircraft type lookup: icao24 -> typecode -> size class.

Sources: OpenSky aircraft database (icao24 -> typecode; last published 2024-11) and
ICAO Doc 8643 type designators (typecode -> wake category, engine type). Size class is
only the fallback grouping for per-type normalisation when a type has too few samples.

    uv run python -m turbcast.ingest.aircraft_db
"""

from __future__ import annotations

import sys

import duckdb
import httpx
import pandas as pd

from turbcast import config

BASE = "https://s3.opensky-network.org/data-samples/metadata"
AIRCRAFT_DB_URL = f"{BASE}/aircraftDatabase.csv"
DOC8643_URL = f"{BASE}/doc8643AircraftTypes.csv"
USER_AGENT = "turbcast/0.1 (research prototype)"

RAW_DIR = "data/raw/metadata"
TYPES_PARQUET = "data/parquet/aircraft_types.parquet"
AIRCRAFT_PARQUET = "data/parquet/aircraft_db.parquet"

SIZE_CLASSES = ("super", "heavy", "medium_jet", "medium_prop", "light", "UNK")
# Doc 8643 here lists the A380 as H; ICAO's 'J' (super) category is applied manually.
SUPER_TYPES = {"A388", "A225"}


def size_class(typecode: str | None, wtc: str | None, engine_type: str | None) -> str:
    if typecode in SUPER_TYPES:
        return "super"
    if wtc == "J":
        return "super"
    if wtc == "H":
        return "heavy"
    if wtc == "M":
        return "medium_jet" if engine_type == "Jet" else "medium_prop"
    if wtc in ("L", "L/M"):
        return "light"
    return "UNK"


def build_type_table(doc8643: pd.DataFrame) -> pd.DataFrame:
    """One row per designator. Where model variants disagree, take the most common
    WTC / engine type (one designator conflicts in the 2024 file)."""
    d = doc8643.rename(
        columns={
            "Designator": "typecode",
            "WTC": "wtc",
            "EngineType": "engine_type",
            "EngineCount": "engine_count",
        }
    )[["typecode", "wtc", "engine_type", "engine_count"]].dropna(subset=["typecode"])
    d["typecode"] = d["typecode"].str.strip().str.upper()

    def mode(s: pd.Series) -> object:
        m = s.dropna().mode()
        return m.iloc[0] if len(m) else None

    t = d.groupby("typecode", as_index=False).agg(
        wtc=("wtc", mode), engine_type=("engine_type", mode), engine_count=("engine_count", mode)
    )
    t["size_class"] = [
        size_class(tc, w, e)
        for tc, w, e in zip(t["typecode"], t["wtc"], t["engine_type"], strict=True)
    ]
    return t


def build_aircraft_table(db: pd.DataFrame, types: pd.DataFrame) -> pd.DataFrame:
    a = db[["icao24", "typecode", "registration", "model"]].copy()
    a["icao24"] = a["icao24"].astype("str").str.strip().str.lower()
    a = a[a["icao24"].str.fullmatch(r"[0-9a-f]{6}", na=False)]
    a["typecode"] = a["typecode"].astype("str").str.strip().str.upper().replace({"": None})
    a = a.drop_duplicates("icao24", keep="last")
    a = a.merge(types[["typecode", "size_class"]], on="typecode", how="left")
    a["size_class"] = a["size_class"].fillna("UNK")
    return a.reset_index(drop=True)


def attach_types(
    records: pd.DataFrame, aircraft: pd.DataFrame, types: pd.DataFrame
) -> pd.DataFrame:
    """Add `typecode_resolved` and `size_class` to a record frame. A typecode sent
    inline by the feed (adsb.lol) wins over the database lookup."""
    out = records.merge(
        aircraft[["icao24", "typecode"]].rename(columns={"typecode": "db_typecode"}),
        on="icao24",
        how="left",
    )
    inline = out["typecode"] if "typecode" in out else pd.Series(pd.NA, index=out.index)
    out["typecode_resolved"] = inline.where(inline.notna() & inline.ne(""), out["db_typecode"])
    out = out.drop(columns="db_typecode").merge(
        types[["typecode", "size_class"]].rename(columns={"typecode": "typecode_resolved"}),
        on="typecode_resolved",
        how="left",
    )
    out["size_class"] = out["size_class"].fillna("UNK")
    return out


def download(force: bool = False) -> None:
    raw = config.resolve(RAW_DIR)
    raw.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=600, headers={"User-Agent": USER_AGENT}) as client:
        for url in (AIRCRAFT_DB_URL, DOC8643_URL):
            path = raw / url.rsplit("/", 1)[1]
            if path.exists() and not force:
                continue
            with client.stream("GET", url) as r:
                r.raise_for_status()
                with open(path, "wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)


def build() -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = config.resolve(RAW_DIR)
    types = build_type_table(pd.read_csv(raw / "doc8643AircraftTypes.csv", dtype=str))
    db = pd.read_csv(
        raw / "aircraftDatabase.csv",
        dtype=str,
        usecols=["icao24", "typecode", "registration", "model"],
    )
    aircraft = build_aircraft_table(db, types)
    for df, rel in ((types, TYPES_PARQUET), (aircraft, AIRCRAFT_PARQUET)):
        p = config.resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p, index=False)
    return aircraft, types


def coverage(source: str = "opensky") -> pd.Series:
    """Share of distinct airborne aircraft in the live archive resolved to a type/class."""
    cfg = config.load()
    glob = f"{config.resolve(cfg.live.out_dir)}/source={source}/**/*.parquet"
    rec = duckdb.sql(
        f"select distinct icao24, typecode from read_parquet('{glob}', hive_partitioning=true) "
        f"where not on_ground"
    ).df()
    aircraft = pd.read_parquet(config.resolve(AIRCRAFT_PARQUET))
    types = pd.read_parquet(config.resolve(TYPES_PARQUET))
    out = attach_types(rec, aircraft, types)
    return pd.Series(
        {
            "aircraft": len(out),
            "typecode_resolved": out["typecode_resolved"].notna().mean(),
            "size_class_known": out["size_class"].ne("UNK").mean(),
        }
    )


def main() -> int:
    download()
    aircraft, types = build()
    print(f"types={len(types):,} aircraft={len(aircraft):,}")
    print(types["size_class"].value_counts().to_string())
    print(coverage().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
