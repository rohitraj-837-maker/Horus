"""PIREP ingestion and turbulence-group parsing.

Sources (same parser for both, applied to the raw report text):
* Iowa Environmental Mesonet archive CSV (historical ground truth).
* aviationweather.gov Data API JSON (live overlay).

Intensity scale (`tb_max`, `tb_min`): NEG 0, LGT 2, MOD 4, SEV 6, EXTRM 8. A joined
range such as LGT-MOD or "LGT TO MOD" scores the midpoint (3). `tb_max` is the
strongest intensity mentioned ("CONS LGT OCNL MOD" -> 4); `tb_min` the weakest.
MOD+ means tb_max >= 4 (rule configurable: AWC's own decoder is inconsistent here).
-1 means the report has no parseable turbulence intensity.

    uv run python -m turbcast.ingest.pireps --start 2026-11-01 --end 2026-11-02
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from turbcast import config

IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/pireps.py"
AWC_URL = "https://aviationweather.gov/api/data/pirep"
USER_AGENT = "turbcast/0.1 (research prototype)"

_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")
NEG, LGT, MOD, SEV, EXTRM = 0, 2, 4, 6, 8
NO_TB = -1
LEVEL_NAMES = {
    0: "NEG",
    1: "NEG-LGT",
    2: "LGT",
    3: "LGT-MOD",
    4: "MOD",
    5: "MOD-SEV",
    6: "SEV",
    7: "SEV-EXTRM",
    8: "EXTRM",
}

_INTENSITY_WORDS = {
    NEG: ["NEGATIVE", "SMOOTH", "SMTH", "NONE", "NEG", "NIL"],
    LGT: ["LIGHT", "LGHT", "LGT", "LIT", "LT", "LG"],
    MOD: ["MODERATE", "MDT", "MOD", "MD"],
    SEV: ["SEVERE", "SVR", "SEV"],
    EXTRM: ["EXTREME", "EXTRM", "XTRM", "EXTM"],
}
_WORD_LEVEL = {w: lvl for lvl, ws in _INTENSITY_WORDS.items() for w in ws}
# Letters-only boundaries so "MOD090" and "LGT-MOD" tokenise, but "MODE" does not.
_INTENSITY_RE = re.compile(
    r"(?<![A-Z])(" + "|".join(sorted(_WORD_LEVEL, key=len, reverse=True)) + r")(?![A-Z])"
)
_RANGE_SEP_RE = re.compile(r"^\s*(?:-|TO)\s*$")
_TYPE_RE = re.compile(r"(?<![A-Z])(CAT|CHOP|CHP|MTN\s*WAVE|MWAVE|LLWS|WS)(?![A-Z])")
_FREQ_RE = re.compile(r"(?<![A-Z])(OCNL|OCCNL|CONS|CONT|CONST|INTMT|INT)(?![A-Z])")
_PHASE_RE = re.compile(r"(?<![A-Z])(DURC|DURD|DURGC|DURGD)(?![A-Z])")

_ALT_RANGE_RE = re.compile(r"(?<![\d])(?:FL)?(\d{2,3})\s*-\s*(?:FL)?(\d{2,3})(?![\d])")
_ALT_SFC_RE = re.compile(r"SFC\s*-\s*(?:FL)?(\d{2,3})")
_ALT_BLO_ABV_RE = re.compile(r"(BLO|BLW|BELOW|ABV|ABOVE)\s*(?:FL)?(\d{2,3})")
_ALT_SINGLE_RE = re.compile(r"(?<![\d-])(?:FL)?(\d{3})(?![\d-])")

_GROUP_RE = re.compile(r"/(OV|TM|FL|TP|SK|WX|TA|WV|TB|IC|RM)\s*([^/]*)")
_AIREP_TB_RE = re.compile(r"(?:^|\s)TB\s+(.*?)(?=\s+(?:IC|SK|RM|WX|TA)(?:\s|$)|$)")
_AIREP_FL_RE = re.compile(r"(?<![A-Z\d])F(\d{3})(?![\d])")

PIREP_SCHEMA = pa.schema(
    [
        pa.field("time", pa.int64(), nullable=False),
        pa.field("lat", pa.float64()),
        pa.field("lon", pa.float64()),
        pa.field("fl_lo", pa.float32()),  # flight level (hundreds of ft)
        pa.field("fl_hi", pa.float32()),
        pa.field("report_fl", pa.float32()),
        pa.field("tb_max", pa.int8()),
        pa.field("tb_min", pa.int8()),
        pa.field("tb_type", pa.string()),
        pa.field("tb_freq", pa.string()),
        pa.field("phase", pa.string()),  # DURC / DURD
        pa.field("aircraft", pa.string()),
        pa.field("urgent", pa.bool_()),
        pa.field("has_tb", pa.bool_()),
        pa.field("parse_ok", pa.bool_()),
        pa.field("source", pa.string()),
        pa.field("raw", pa.string()),
    ]
)


# --------------------------------------------------------------------------- parsing


def parse_intensity(text: str) -> tuple[int, int]:
    """(max, min) intensity on the 0-8 scale, or (NO_TB, NO_TB) if none found."""
    t = text.upper()
    hits = [(m.start(), m.end(), _WORD_LEVEL[m.group(1)]) for m in _INTENSITY_RE.finditer(t)]
    if not hits:
        return NO_TB, NO_TB
    levels: list[int] = []
    i = 0
    while i < len(hits):
        s, e, lvl = hits[i]
        if i + 1 < len(hits) and _RANGE_SEP_RE.match(t[e : hits[i + 1][0]]):
            levels.append((lvl + hits[i + 1][2]) // 2)  # joined range -> midpoint
            i += 2
        else:
            levels.append(lvl)
            i += 1
    return max(levels), min(levels)


def _fl(v: str) -> float:
    return float(int(v))


def parse_altitudes(text: str, report_fl: float) -> tuple[float, float]:
    """Altitude band (FL) stated inside the TB group; NaN if none."""
    t = text.upper()
    if m := _ALT_SFC_RE.search(t):
        return 0.0, _fl(m.group(1))
    if m := _ALT_BLO_ABV_RE.search(t):
        v = _fl(m.group(2))
        if m.group(1).startswith("B"):
            return 0.0, v
        top = report_fl if np.isfinite(report_fl) and report_fl > v else v + 20.0
        return v, top
    if m := _ALT_RANGE_RE.search(t):
        a, b = _fl(m.group(1)), _fl(m.group(2))
        return min(a, b), max(a, b)
    if m := _ALT_SINGLE_RE.search(t):
        v = _fl(m.group(1))
        return v, v
    return np.nan, np.nan


def parse_report(raw: str) -> dict[str, Any]:
    """Parse one raw PIREP/AIREP text into turbulence + altitude fields."""
    text = (raw or "").upper().replace('"', " ")
    groups: dict[str, str] = {}
    for m in _GROUP_RE.finditer(text):
        groups.setdefault(m.group(1), m.group(2).strip())

    report_fl = np.nan
    fl_txt = groups.get("FL", "")
    if m := re.search(r"(\d{3})", fl_txt):
        report_fl = _fl(m.group(1))
    elif "FL" not in groups and (m := _AIREP_FL_RE.search(text)):
        report_fl = _fl(m.group(1))

    tb = groups.get("TB")
    if tb is None and (m := _AIREP_TB_RE.search(text)):
        tb = m.group(1).strip()
    has_tb = tb is not None
    tb = tb or ""

    tb_max, tb_min = parse_intensity(tb)
    lo, hi = parse_altitudes(tb, report_fl)
    if not np.isfinite(lo) and np.isfinite(report_fl):
        lo = hi = report_fl

    phase_m = _PHASE_RE.search(" ".join([tb, fl_txt, groups.get("RM", "")]))
    phase = None
    if phase_m:
        phase = "DURC" if "C" in phase_m.group(1)[3:] else "DURD"
    type_m = _TYPE_RE.search(tb)
    tb_type = None
    if type_m:
        tb_type = {"CHP": "CHOP", "MWAVE": "MTN WAVE"}.get(type_m.group(1), type_m.group(1))
        tb_type = re.sub(r"\s+", " ", tb_type)
    freq_m = _FREQ_RE.search(tb)
    freq = None
    if freq_m:
        f = freq_m.group(1)
        freq = {"OCCNL": "OCNL", "CONT": "CONS", "CONST": "CONS", "INT": "INTMT"}.get(f, f)

    return {
        "fl_lo": lo,
        "fl_hi": hi,
        "report_fl": report_fl,
        "tb_max": tb_max,
        "tb_min": tb_min,
        "tb_type": tb_type,
        "tb_freq": freq,
        "phase": phase,
        "tp": groups.get("TP", "").split(" ")[0] or None,
        "has_tb": has_tb,
        "parse_ok": (not has_tb) or tb_max != NO_TB,
    }


# --------------------------------------------------------------------------- sources


def _finish(base: pd.DataFrame, raws: pd.Series, source: str) -> pd.DataFrame:
    parsed = pd.DataFrame([parse_report(r) for r in raws], index=base.index)
    out = pd.concat([base, parsed], axis=1)
    out["aircraft"] = out["aircraft"].where(out["aircraft"].notna(), out.pop("tp"))
    # IEM gives FL in feet as a fallback when the text has no usable altitude.
    if "fl_ft" in out:
        fb = out.pop("fl_ft") / 100.0
        missing = out["fl_lo"].isna() & fb.notna()
        out.loc[missing, ["fl_lo", "fl_hi"]] = np.c_[fb[missing], fb[missing]]
        out["report_fl"] = out["report_fl"].fillna(fb)
    out["source"] = source
    out["raw"] = raws.values
    return _cast(out)


def _cast(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for f in PIREP_SCHEMA:
        col = df[f.name]
        if f.type == pa.string():
            out[f.name] = col.astype(object).where(col.notna(), None).astype("str")
        elif f.type == pa.bool_():
            out[f.name] = col.fillna(False).astype(bool)
        else:
            out[f.name] = col.astype(f.type.to_pandas_dtype())
    return out.reset_index(drop=True)


def read_iem_csv(text: str) -> pd.DataFrame:
    """Parse IEM PIREP CSV. Quoting is ignored (reports contain stray '"'); fields
    are split from both ends so a comma inside REPORT cannot shift the columns."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return _cast(pd.DataFrame({f.name: [] for f in PIREP_SCHEMA}))
    header = lines[0].split(",")
    # VALID,URGENT,AIRCRAFT | REPORT,ICING,TURBULENCE (free text) | ATRCC..LON (clean)
    n_tail = len(header) - header.index("ATRCC")
    rows = []
    for ln in lines[1:]:
        parts = ln.split(",")
        middle = parts[3:-n_tail]
        # ICING/TURBULENCE are copies of REPORT groups; REPORT is everything before them.
        report = ",".join(middle[:-2]) if len(middle) >= 3 else ",".join(middle)
        rows.append([*parts[:3], report, *parts[-n_tail:]])
    cols = ["VALID", "URGENT", "AIRCRAFT", "REPORT", *header[-n_tail:]]
    d = pd.DataFrame(rows, columns=cols)
    base = pd.DataFrame(
        {
            # Unit-safe epoch seconds (pandas 3 may parse at us, not ns, resolution).
            "time": (pd.to_datetime(d["VALID"], format="%Y%m%d%H%M", utc=True) - _EPOCH)
            // pd.Timedelta(seconds=1),
            "lat": pd.to_numeric(d["LAT"], errors="coerce"),
            "lon": pd.to_numeric(d["LON"], errors="coerce"),
            "urgent": d["URGENT"].eq("T"),
            "aircraft": d["AIRCRAFT"].replace({"None": None, "": None}),
            "fl_ft": pd.to_numeric(d["FL"], errors="coerce"),
        }
    )
    return _finish(base, d["REPORT"], "iem")


def read_awc_json(items: list[dict[str, Any]]) -> pd.DataFrame:
    if not items:
        return _cast(pd.DataFrame({f.name: [] for f in PIREP_SCHEMA}))
    d = pd.DataFrame(items)
    fl = pd.to_numeric(d["fltLvl"], errors="coerce")
    base = pd.DataFrame(
        {
            "time": pd.to_numeric(d["obsTime"]).astype("int64"),
            "lat": pd.to_numeric(d["lat"], errors="coerce"),
            "lon": pd.to_numeric(d["lon"], errors="coerce"),
            "urgent": d.get("pirepType", pd.Series("", index=d.index)).eq("Urgent PIREP"),
            "aircraft": d["acType"].replace({"": None}),
            "fl_ft": fl.where(fl > 0) * 100.0,
        }
    )
    return _finish(base, d["rawOb"], "awc")


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """The same PIREP is re-issued in several products; keep one copy."""
    key = pd.DataFrame(
        {
            "t": df["time"],
            "lat": df["lat"].round(2),
            "lon": df["lon"].round(2),
            "ac": df["aircraft"].fillna(""),
            "lo": df["fl_lo"].fillna(-1),
            "hi": df["fl_hi"].fillna(-1),
            "tb": df["tb_max"],
        }
    )
    return df[~key.duplicated()].reset_index(drop=True)


def is_positive(df: pd.DataFrame, min_level: int = MOD) -> pd.Series:
    return df["tb_max"] >= min_level


def parse_stats(df: pd.DataFrame) -> dict[str, float]:
    tb = df[df["has_tb"]]
    return {
        "reports": float(len(df)),
        "with_tb": float(len(tb)),
        "tb_parse_fail_rate": float((~tb["parse_ok"]).mean()) if len(tb) else 0.0,
        "mod_plus": float(is_positive(df).sum()),
        "no_altitude": float(df["fl_lo"].isna().mean()) if len(df) else 0.0,
    }


# --------------------------------------------------------------------------- download


def fetch_iem_day(client: httpx.Client, day: date) -> str:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    r = client.get(
        IEM_URL,
        params={
            "sts": f"{start:%Y-%m-%dT%H:%MZ}",
            "ets": f"{end:%Y-%m-%dT%H:%MZ}",
            "fmt": "csv",
            "artcc": "_ALL",
        },
    )
    r.raise_for_status()
    return r.text


def fetch_awc_recent(client: httpx.Client, cfg: config.Config, hours: int = 2) -> pd.DataFrame:
    r = cfg.region
    resp = client.get(
        AWC_URL,
        params={
            "format": "json",
            "age": hours,
            "bbox": f"{r.lat_min},{r.lon_min},{r.lat_max},{r.lon_max}",
        },
    )
    resp.raise_for_status()
    return dedupe(read_awc_json(resp.json() if resp.content.strip() else []))


def ingest_iem(start: date, end: date, out_dir: str = "data/parquet/pireps") -> pd.DataFrame:
    """Download [start, end) day by day to data/parquet/pireps/date=YYYY-MM-DD.parquet."""
    out = config.resolve(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    with httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT}) as client:
        day = start
        while day < end:
            df = dedupe(read_iem_csv(fetch_iem_day(client, day)))
            table = pa.Table.from_pandas(df, schema=PIREP_SCHEMA, preserve_index=False)
            pq.write_table(table, out / f"date={day:%Y-%m-%d}.parquet", compression="zstd")
            frames.append(df)
            print(f"{day}: {parse_stats(df)}", file=sys.stderr)
            day += timedelta(days=1)
    return pd.concat(frames, ignore_index=True) if frames else read_iem_csv("")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True, help="exclusive")
    args = ap.parse_args()
    df = ingest_iem(args.start, args.end)
    print(parse_stats(df))
    return 0


if __name__ == "__main__":
    sys.exit(main())
