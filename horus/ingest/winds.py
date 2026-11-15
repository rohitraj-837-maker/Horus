"""GFS upper-air winds from the Open-Meteo historical forecast API (SPEC §5.5 rule 2).

    uv run python -m horus.ingest.winds --start 2025-12-02 --end 2026-01-15

Downloads wind speed/direction at winds.levels_hpa on a regular lat/lon lattice
(winds.lattice_step_deg) covering the region, hourly, and saves one compressed
file data/winds/<start>_<end>.npz with u, v (m/s) as (time, level, lat, lon).
Using Open-Meteo avoids GRIB decoding (eccodes) on the laptop; the 400 hPa level is
a logged deviation from SPEC (it covers FL200-FL260).
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from horus import config
from horus.config import Config
from horus.core.winds import WindField, uv_from_speed_dir

URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
USER_AGENT = "horus/0.1 (research prototype)"
BATCH = 50  # locations per request
OUT_DIR = "data/winds"


def lattice(cfg: Config, step_deg: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    r = cfg.region
    s = step_deg if step_deg is not None else cfg.winds.lattice_step_deg
    lat = np.arange(r.lat_min, r.lat_max + 1e-9, s)
    lon = np.arange(r.lon_min, r.lon_max + 1e-9, s)
    return lat, lon


def variables(levels: list[int]) -> list[str]:
    return [f"wind_{k}_{lv}hPa" for lv in levels for k in ("speed", "direction")]


def parse(
    items: list[dict[str, Any]], levels: list[int]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(time epoch s, u, v) with u/v shaped (time, level, location)."""
    times = np.array(
        [
            datetime.fromisoformat(t).replace(tzinfo=UTC).timestamp()
            for t in items[0]["hourly"]["time"]
        ]
    )
    nt, nl, nloc = len(times), len(levels), len(items)
    u = np.full((nt, nl, nloc), np.nan, dtype=np.float32)
    v = np.full((nt, nl, nloc), np.nan, dtype=np.float32)
    for j, it in enumerate(items):
        h = it["hourly"]
        if h["time"] != items[0]["hourly"]["time"]:
            raise ValueError("locations returned different time axes")
        for k, lv in enumerate(levels):
            spd = np.array(h[f"wind_speed_{lv}hPa"], dtype=float)
            dirn = np.array(h[f"wind_direction_{lv}hPa"], dtype=float)
            uu, vv = uv_from_speed_dir(spd, dirn)
            u[:, k, j], v[:, k, j] = uu, vv
    return times, u, v


def fetch(
    client: httpx.Client,
    lats: list[float],
    lons: list[float],
    start: date,
    end: date,
    levels: list[int],
) -> list[dict[str, Any]]:
    params = {
        "latitude": ",".join(f"{x:g}" for x in lats),
        "longitude": ",".join(f"{x:g}" for x in lons),
        "start_date": str(start),
        "end_date": str(end),
        "hourly": ",".join(variables(levels)),
        "models": "gfs_seamless",
        "wind_speed_unit": "ms",
        "timezone": "GMT",
    }
    for attempt in range(5):
        r = client.get(URL, params=params)
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else [data]
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(min(60.0, 5.0 * 2**attempt))
            continue
        raise RuntimeError(f"Open-Meteo HTTP {r.status_code}: {r.text[:200]}")
    raise RuntimeError("Open-Meteo: retries exhausted")


def download(
    cfg: Config,
    start: date,
    end: date,
    client: httpx.Client | None = None,
    step_deg: float | None = None,
) -> WindField:
    lat, lon = lattice(cfg, step_deg)
    levels = list(cfg.winds.levels_hpa)
    pts = [(a, b) for a in lat for b in lon]
    own = client is None
    cl = client or httpx.Client(timeout=120.0, headers={"User-Agent": USER_AGENT})
    try:
        parts = []
        for i in range(0, len(pts), BATCH):
            chunk = pts[i : i + BATCH]
            items = fetch(cl, [p[0] for p in chunk], [p[1] for p in chunk], start, end, levels)
            parts.append(parse(items, levels))
            print(f"  winds: {min(i + BATCH, len(pts))}/{len(pts)} locations", flush=True)
    finally:
        if own:
            cl.close()
    times = parts[0][0]
    u = np.concatenate([p[1] for p in parts], axis=2).reshape(
        len(times), len(levels), len(lat), len(lon)
    )
    v = np.concatenate([p[2] for p in parts], axis=2).reshape(
        len(times), len(levels), len(lat), len(lon)
    )
    return WindField(times, np.array(levels, dtype=float), lat, lon, u, v)


def path(cfg: Config, start: date, end: date) -> Path:
    return config.resolve(OUT_DIR) / f"{start}_{end}.npz"


def save(w: WindField, p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(p, time=w.time, levels_hpa=w.levels_hpa, lat=w.lat, lon=w.lon, u=w.u, v=w.v)


def load(p: Path) -> WindField:
    z = np.load(p)
    return WindField(z["time"], z["levels_hpa"], z["lat"], z["lon"], z["u"], z["v"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    a = ap.parse_args()
    cfg = config.load()
    s, e = date.fromisoformat(a.start), date.fromisoformat(a.end)
    w = download(cfg, s, e)
    nan = float(np.isnan(w.u).mean())
    save(w, path(cfg, s, e))
    print(f"saved {path(cfg, s, e)}: {w.u.shape} (time, level, lat, lon), NaN {nan:.2%}")
    return 0 if nan < 0.01 else 1


if __name__ == "__main__":
    raise SystemExit(main())
