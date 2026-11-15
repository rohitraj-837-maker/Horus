"""Live ADS-B snapshot sources and the Plan B recorder.

Two sources share one interface (`fetch(client) -> (record_frame, meta)`):

* OpenSky REST `/states/all` (CONUS bbox; 4 credits per call, measured 2026-11-15).
* adsb.lol `/v2/point` tiles (250 nm max radius, so CONUS needs ~35 tiles).

Converters (`opensky_to_record`, `adsblol_to_record`) are pure and unit-tested on
recorded real responses. The recorder writes hourly Hive-partitioned Parquet plus a
per-poll CSV log that `scripts/check_recorder.py` audits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from horus import schema
from horus.config import Config, Region, resolve
from horus.schema import FPM_PER_MPS, FT_PER_M, KT_PER_MPS, RECORD_SCHEMA

log = logging.getLogger(__name__)

OPENSKY_STATES_URL = "https://opensky-network.org/api/states/all"
OPENSKY_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
)
ADSBLOL_POINT_URL = "https://api.adsb.lol/v2/point/{lat:.3f}/{lon:.3f}/{radius:.0f}"
USER_AGENT = "horus/0.1 (research prototype)"

EARTH_RADIUS_KM = 6371.0088
KM_PER_DEG_LAT = math.pi * EARTH_RADIUS_KM / 180.0
KM_PER_NM = 1.852
DAY_S = 86400

# OpenSky state vector field order (REST API docs).
OPENSKY_FIELDS = [
    "icao24",
    "callsign",
    "origin_country",
    "time_position",
    "last_contact",
    "lon",
    "lat",
    "baro_altitude",
    "on_ground",
    "velocity",
    "true_track",
    "vertical_rate",
    "sensors",
    "geo_altitude",
    "squawk",
    "spi",
    "position_source",
]
OPENSKY_POSITION_SOURCE = {0: "adsb", 1: "asterix", 2: "mlat", 3: "flarm"}

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


# --------------------------------------------------------------------------- converters


def _clean_str(s: pd.Series) -> pd.Series:
    s = s.astype(object).where(s.notna(), None)
    return s.map(lambda v: v.strip() or None if isinstance(v, str) else None)


def opensky_to_record(payload: dict[str, Any], rate_hz: float) -> pd.DataFrame:
    """Convert an OpenSky `/states/all` JSON payload into RECORD_SCHEMA rows.

    `time` is the aircraft's last message time (`last_contact`), so repeated polls
    of an aircraft that has gone quiet produce duplicate keys that callers dedupe.
    """
    states = payload.get("states") or []
    if not states:
        return schema.empty(RECORD_SCHEMA)
    snap = int(payload["time"])
    raw = pd.DataFrame([row[: len(OPENSKY_FIELDS)] for row in states], columns=OPENSKY_FIELDS)
    num = {
        c: pd.to_numeric(raw[c], errors="coerce")
        for c in [
            "time_position",
            "last_contact",
            "lon",
            "lat",
            "baro_altitude",
            "velocity",
            "true_track",
            "vertical_rate",
            "geo_altitude",
            "position_source",
        ]
    }
    last_contact = num["last_contact"].fillna(snap)
    out = pd.DataFrame(
        {
            "icao24": raw["icao24"].astype(str).str.strip().str.lower(),
            "time": last_contact.astype(np.int64),
            "lat": num["lat"],
            "lon": num["lon"],
            "baro_alt_ft": num["baro_altitude"] * FT_PER_M,
            "vr_fpm": num["vertical_rate"] * FPM_PER_MPS,
            "gs_kt": num["velocity"] * KT_PER_MPS,
            "track_deg": num["true_track"],
            "on_ground": raw["on_ground"].fillna(False).astype(bool),
            "source": "opensky_live",
            "rate_hz": rate_hz,
            "callsign": _clean_str(raw["callsign"]),
            "typecode": None,
            "snapshot_time": snap,
            "pos_age_s": last_contact - num["time_position"],
            "contact_age_s": snap - last_contact,
            "geo_alt_ft": num["geo_altitude"] * FT_PER_M,
            "msg_type": num["position_source"].map(OPENSKY_POSITION_SOURCE),
        }
    )
    return schema.cast(out, RECORD_SCHEMA)


def adsblol_to_record(payload: dict[str, Any], rate_hz: float) -> pd.DataFrame:
    """Convert an adsb.lol (readsb-format) `/v2/point` payload into RECORD_SCHEMA rows.

    adsb.lol already reports feet, ft/min and knots, so no unit conversion is needed.
    Non-ICAO addresses (hex starting with '~') are dropped: they can't be typed.
    """
    aircraft = [a for a in payload.get("ac") or [] if not str(a.get("hex", "~")).startswith("~")]
    if not aircraft:
        return schema.empty(RECORD_SCHEMA)
    now_s = float(payload["now"]) / 1000.0
    raw = pd.DataFrame(aircraft)

    def col(name: str) -> pd.Series:
        return raw[name] if name in raw.columns else pd.Series(np.nan, index=raw.index)

    alt = col("alt_baro")
    on_ground = alt.astype(str).eq("ground")
    seen = pd.to_numeric(col("seen"), errors="coerce").fillna(0.0)
    seen_pos = pd.to_numeric(col("seen_pos"), errors="coerce")
    out = pd.DataFrame(
        {
            "icao24": raw["hex"].astype(str).str.strip().str.lower(),
            "time": np.round(now_s - seen).astype(np.int64),
            "lat": pd.to_numeric(col("lat"), errors="coerce"),
            "lon": pd.to_numeric(col("lon"), errors="coerce"),
            "baro_alt_ft": pd.to_numeric(alt.where(~on_ground), errors="coerce"),
            "vr_fpm": pd.to_numeric(col("baro_rate"), errors="coerce"),
            "gs_kt": pd.to_numeric(col("gs"), errors="coerce"),
            "track_deg": pd.to_numeric(col("track"), errors="coerce"),
            "on_ground": on_ground,
            "source": "adsblol_live",
            "rate_hz": rate_hz,
            "callsign": _clean_str(col("flight")),
            "typecode": _clean_str(col("t")),
            "snapshot_time": int(round(now_s)),
            "pos_age_s": seen_pos - seen,
            "contact_age_s": seen,
            "geo_alt_ft": pd.to_numeric(col("alt_geom"), errors="coerce"),
            "msg_type": _clean_str(col("type")),
        }
    )
    return schema.cast(out, RECORD_SCHEMA)


def dedupe(record: pd.DataFrame) -> pd.DataFrame:
    """One row per (icao24, time): keep the freshest (smallest contact age)."""
    if record.empty:
        return record
    return (
        record.sort_values(["icao24", "time", "contact_age_s"], na_position="last")
        .drop_duplicates(["icao24", "time"], keep="first")
        .reset_index(drop=True)
    )


# --------------------------------------------------------------------------- tiling


def haversine_km(lat1: Any, lon1: Any, lat2: Any, lon2: Any) -> Any:
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def cover_tiles(region: Region, radius_nm: float) -> list[tuple[float, float]]:
    """Circle centres (hex packing) whose radius-`radius_nm` discs cover `region`.

    Hex packing of circles of radius r covers the plane when rows are 1.5 r apart and
    centres sqrt(3) r apart within a row, with alternate rows offset by half a step.
    One longitude spacing is used for every row so the half-step offsets stay aligned
    (per-row spacing drifts and leaves holes). A degree of longitude is longest nearest
    the equator, so the spacing in degrees is set at the lowest |lat| any row reaches;
    elsewhere tiles are just denser. The 0.97 factor is margin for the flat-plane
    approximation.
    """
    r_km = radius_nm * KM_PER_NM * 0.97
    dlat = 1.5 * r_km / KM_PER_DEG_LAT
    spans_equator = region.lat_min < 0 < region.lat_max
    low = 0.0 if spans_equator else min(abs(region.lat_min), abs(region.lat_max))
    dlon = math.sqrt(3) * r_km / (KM_PER_DEG_LAT * math.cos(math.radians(low)))
    tiles: list[tuple[float, float]] = []
    row = 0
    lat = region.lat_min
    while True:
        lon = region.lon_min - (dlon / 2 if row % 2 else 0.0)
        while True:
            tiles.append((round(lat, 4), round(lon, 4)))
            if lon >= region.lon_max:
                break
            lon += dlon
        if lat >= region.lat_max:
            break
        lat += dlat
        row += 1
    return tiles


# --------------------------------------------------------------------------- auth + pacing


@dataclass(frozen=True)
class Credentials:
    client_id: str
    client_secret: str = field(repr=False)


def load_credentials(path: str | Path) -> Credentials | None:
    """Read an OpenSky API-client JSON file; None if the file is absent.

    Accepts `clientId`/`clientSecret` (OpenSky download) or snake_case keys.
    Error messages name keys only, never values.
    """
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    cid = data.get("clientId", data.get("client_id"))
    sec = data.get("clientSecret", data.get("client_secret"))
    if not cid or not sec:
        raise ValueError(
            f"{p.name}: expected keys clientId/clientSecret (or client_id/client_secret); "
            f"found keys {sorted(data)}"
        )
    return Credentials(str(cid), str(sec))


class TokenManager:
    """OAuth2 client-credentials token cache for OpenSky (refreshes 60 s early)."""

    def __init__(self, creds: Credentials | None, now: Clock = time.time) -> None:
        self.creds = creds
        self.now = now
        self._token: str | None = None
        self._expires = 0.0

    def invalidate(self) -> None:
        self._token = None

    async def headers(self, client: httpx.AsyncClient) -> dict[str, str]:
        if self.creds is None:
            return {}
        if self._token is None or self.now() >= self._expires - 60:
            r = await client.post(
                OPENSKY_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.creds.client_id,
                    "client_secret": self.creds.client_secret,
                },
            )
            r.raise_for_status()
            body = r.json()
            self._token = str(body["access_token"])
            self._expires = self.now() + float(body.get("expires_in", 1800))
        return {"Authorization": f"Bearer {self._token}"}


def opensky_interval(remaining: int | None, now: float, floor_s: float, cost: int) -> float:
    """Seconds until the next poll so credits last until the 00:00 UTC reset."""
    if remaining is None:
        return floor_s
    secs_left = DAY_S - (now % DAY_S)
    calls = remaining // cost
    if calls <= 0:
        return secs_left + 5.0
    return max(floor_s, secs_left / calls)


class RateLimited(Exception):
    def __init__(self, retry_after: float | None) -> None:
        super().__init__(f"rate limited (retry after {retry_after})")
        self.retry_after = retry_after


# --------------------------------------------------------------------------- sources


class Source(Protocol):
    name: str

    async def fetch(self, client: httpx.AsyncClient) -> tuple[pd.DataFrame, dict[str, Any]]: ...

    def next_interval(self, meta: dict[str, Any]) -> float: ...


class OpenSkySource:
    name = "opensky"

    def __init__(self, cfg: Config, tokens: TokenManager, now: Clock = time.time) -> None:
        self.cfg = cfg
        self.tokens = tokens
        self.now = now
        r = cfg.region
        self.params = {
            "lamin": r.lat_min,
            "lomin": r.lon_min,
            "lamax": r.lat_max,
            "lomax": r.lon_max,
        }
        self.interval = float(cfg.live.poll_interval_s)

    async def fetch(self, client: httpx.AsyncClient) -> tuple[pd.DataFrame, dict[str, Any]]:
        headers = await self.tokens.headers(client)
        r = await client.get(OPENSKY_STATES_URL, params=self.params, headers=headers)
        rem = r.headers.get("x-rate-limit-remaining")
        meta: dict[str, Any] = {"credits_remaining": int(rem) if rem is not None else None}
        if r.status_code == 429:
            ra = r.headers.get("x-rate-limit-retry-after-seconds")
            raise RateLimited(float(ra) if ra else None)
        if r.status_code == 401:
            self.tokens.invalidate()
        r.raise_for_status()
        df = dedupe(opensky_to_record(r.json(), rate_hz=1.0 / self.interval))
        return df, meta

    def next_interval(self, meta: dict[str, Any]) -> float:
        self.interval = opensky_interval(
            meta.get("credits_remaining"),
            self.now(),
            self.cfg.live.poll_interval_s,
            self.cfg.live.opensky_bbox_credit_cost,
        )
        return self.interval


class AdsbLolSource:
    name = "adsblol"

    def __init__(self, cfg: Config, sleep: Sleep = asyncio.sleep) -> None:
        self.cfg = cfg
        self.sleep = sleep
        self.tiles = cover_tiles(cfg.region, cfg.live.adsblol_radius_nm)
        self.interval = float(cfg.live.poll_interval_s)

    async def fetch(self, client: httpx.AsyncClient) -> tuple[pd.DataFrame, dict[str, Any]]:
        frames, failed = [], 0
        rate = 1.0 / self.interval
        for i, (lat, lon) in enumerate(self.tiles):
            if i:
                await self.sleep(self.cfg.live.adsblol_request_spacing_s)
            url = ADSBLOL_POINT_URL.format(lat=lat, lon=lon, radius=self.cfg.live.adsblol_radius_nm)
            try:
                r = await client.get(url)
                if r.status_code == 429:
                    raise RateLimited(None)
                r.raise_for_status()
                frames.append(adsblol_to_record(r.json(), rate_hz=rate))
            except (httpx.HTTPError, ValueError) as e:
                failed += 1
                log.warning("adsb.lol tile %s,%s failed: %s", lat, lon, e)
        if failed == len(self.tiles):
            raise httpx.HTTPError(f"all {failed} adsb.lol tiles failed")
        df = pd.concat(frames, ignore_index=True) if frames else schema.empty(RECORD_SCHEMA)
        # Same aircraft seen from overlapping tiles: keep the freshest copy.
        df = df.sort_values("contact_age_s").drop_duplicates("icao24").reset_index(drop=True)
        return df, {"tiles": len(self.tiles), "tiles_failed": failed, "credits_remaining": None}

    def next_interval(self, meta: dict[str, Any]) -> float:
        return self.interval


# --------------------------------------------------------------------------- recorder


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)


class Recorder:
    """Buffers snapshots and writes hourly Hive-partitioned Parquet + a poll log."""

    def __init__(self, out_dir: str | Path, source_name: str) -> None:
        self.root = Path(out_dir) / f"source={source_name}"
        self.log_dir = Path(out_dir) / "_polls" / f"source={source_name}"
        self.source_name = source_name
        self.buffer: list[pd.DataFrame] = []

    def add(self, df: pd.DataFrame) -> None:
        if not df.empty:
            self.buffer.append(df)

    def log_poll(
        self,
        poll_time: float,
        ok: bool,
        n: int,
        meta: dict[str, Any],
        interval: float,
        error: str = "",
    ) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"{_utc(poll_time):%Y-%m-%d}.csv"
        new = not path.exists()
        with open(path, "a") as f:
            if new:
                f.write("poll_time,ok,n_rows,credits_remaining,interval_s,error\n")
            cr = meta.get("credits_remaining")
            cr_s = "" if cr is None else str(cr)
            err = error.replace(",", ";").replace("\n", " ")[:200]
            f.write(f"{poll_time:.0f},{int(ok)},{n},{cr_s},{interval:.1f},{err}\n")

    def flush(self) -> list[Path]:
        """Write buffered rows; on failure keep the buffer and return []."""
        if not self.buffer:
            return []
        df = pd.concat(self.buffer, ignore_index=True)
        hours = (df["snapshot_time"] // 3600).to_numpy()
        written = []
        try:
            for h in np.unique(hours):
                part = df[hours == h]
                start = _utc(float(h) * 3600)
                d = self.root / f"date={start:%Y-%m-%d}" / f"hour={start:%H}"
                d.mkdir(parents=True, exist_ok=True)
                path = d / f"part-{int(part['snapshot_time'].min())}-{len(part)}.parquet"
                table = pa.Table.from_pandas(part, schema=RECORD_SCHEMA, preserve_index=False)
                pq.write_table(table, path, compression="zstd")
                written.append(path)
        except OSError as e:
            log.error("flush failed, keeping %d buffered rows: %s", len(df), e)
            return []
        self.buffer.clear()
        return written


async def run(
    cfg: Config,
    source: Source,
    duration_s: float | None = None,
    client: httpx.AsyncClient | None = None,
    now: Clock = time.time,
    sleep: Sleep | None = None,
    stop: asyncio.Event | None = None,
) -> Recorder:
    """Poll `source` until `duration_s` elapses or `stop` is set. Never raises on
    network errors: it backs off exponentially (or per Retry-After) and keeps going."""
    stop = stop or asyncio.Event()

    async def _sleep_default(dt: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=dt)
        except TimeoutError:
            pass

    sleep_fn: Sleep = sleep or _sleep_default
    rec = Recorder(resolve(cfg.live.out_dir), source.name)
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=60, headers={"User-Agent": USER_AGENT})
    start = last_flush = now()
    backoff = 0.0
    try:
        while not stop.is_set() and (duration_s is None or now() - start < duration_s):
            t0 = now()
            meta: dict[str, Any] = {}
            try:
                df, meta = await source.fetch(client)
                rec.add(df)
                backoff = 0.0
                interval = source.next_interval(meta)
                rec.log_poll(t0, True, len(df), meta, interval)
            except (httpx.HTTPError, RateLimited, ValueError, KeyError) as e:
                backoff = min(max(backoff * 2, 5.0), float(cfg.live.backoff_max_s))
                retry_after = getattr(e, "retry_after", None)
                interval = max(backoff, retry_after or 0.0)
                rec.log_poll(t0, False, 0, meta, interval, f"{type(e).__name__}: {e}")
                log.warning("poll failed (%s); retrying in %.0f s", e, interval)
            if now() - last_flush >= cfg.live.flush_interval_s:
                rec.flush()
                last_flush = now()
            await sleep_fn(max(0.0, interval - (now() - t0)))
    finally:
        rec.flush()
        if own_client:
            await client.aclose()
    return rec
