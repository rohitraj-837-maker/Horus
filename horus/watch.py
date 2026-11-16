"""Horus as a plain text stream: one line per event, nothing to redraw.

    make watch                       live: replay an archived test day at 30x
    make watch ARGS="--fl 340"       only events within 2,000 ft of FL340
    make cases                       the three curated case studies, told minute by minute

Same hub as the API (horus.api.main.build_hub): archive feed -> live loop -> grid.
Lines: TURBULENCE (an aircraft's own vertical-speed data turned bumpy), calm (it
settled), FORECAST (a cell well above its normal chance), PILOT REPORT (real PIREP,
checked against what Horus saw in the minutes before, never fed in), and a status
line every few minutes.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from typing import IO, Any

import h3
import numpy as np
import pandas as pd

from horus import config
from horus.config import Watch
from horus.core import geo, grid
from horus.eval import passes
from horus.places import Places

LEVEL = {4: "moderate", 5: "moderate-severe", 6: "severe", 7: "severe-extreme", 8: "extreme"}
LEVEL_WORD = {"MOD": "moderate", "SEV": "severe", "EXTRM": "extreme"}
COLOUR = {
    "hit": "1;38;5;214",  # amber
    "calm": "2",
    "fc": "38;5;179",  # gold
    "pirep": "1;38;5;209",  # orange
    "pirep_sev": "1;38;5;203",  # red
    "status": "2",
    "head": "1",
}
LEGEND = """\
  TURBULENCE    an aircraft's own climb/descent data turned bumpy (strength vs the threshold)
  calm          a bump that lasted 2 min or more has ended
  FORECAST      a ~45 km cell where the next aircraft is likely to hit turbulence ({h} min)
  PILOT REPORT  a real pilot report (FL200+): never fed in, only used to check Horus"""


def hhmm(t: float) -> str:
    return time.strftime("%H:%MZ", time.gmtime(t))


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 or word == 'aircraft' else 's'}"


def fl_range(lo: float, hi: float) -> str:
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return "altitude unknown"
    return f"FL{int(lo):03d}" if int(lo) == int(hi) else f"FL{int(lo):03d}-{int(hi):03d}"


@dataclass
class Line:
    t: float
    kind: str
    label: str
    text: str


class Printer:
    def __init__(self, out: IO[str], colour: bool) -> None:
        self.out, self.colour = out, colour

    def paint(self, s: str, kind: str) -> str:
        code = COLOUR.get(kind, "")
        return f"\x1b[{code}m{s}\x1b[0m" if self.colour and code else s

    def raw(self, s: str = "", kind: str = "") -> None:
        print(self.paint(s, kind), file=self.out, flush=True)

    def line(self, ln: Line, stamp: str | None = None) -> None:
        self.raw(f"{stamp or hhmm(ln.t):>7}  {ln.label:<12}  {ln.text}", ln.kind)


@dataclass
class Hit:
    start: int  # first detected minute
    last: int  # latest detected minute
    peak: float


@dataclass
class Watcher:
    """Turns hub state into event lines; call update() after each hub step.

    The clock is the grid's tick, not the feed time: a minute of data is final only
    once it has settled (live_loop.settle_s), and only settled minutes reach the grid
    and the evaluation. So the stream runs ~5 min behind the flights, and every
    TURBULENCE line is a minute the grid really used.
    """

    hub: Any
    places: Places
    w: Watch
    fl: int | None = None  # band filter: events within 2,000 ft of this flight level
    active: dict[str, Hit] = field(default_factory=dict)
    recent: pd.DataFrame = field(default_factory=pd.DataFrame)  # settled aircraft-minutes
    lit: set[int] = field(default_factory=set)
    pi: int = -1
    last_tick: int = -1
    next_status: int = -1
    n_hot: int = 0
    n_hits: int = 0
    n_reports: int = 0
    n_covered: int = 0  # reports with an aircraft passing nearby just before
    n_backed: int = 0  # ... and one of them detecting

    def __post_init__(self) -> None:
        g = self.hub.cfg.grid
        self.bins_fl = np.arange(g.fl_min, g.fl_max, g.fl_bin)
        keep = np.ones(len(self.bins_fl), bool)
        if self.fl is not None:
            keep = (self.bins_fl + g.fl_bin > self.fl - 20) & (self.bins_fl < self.fl + 20)
        self.bins = np.flatnonzero(keep)
        self.index = {int(c): j for j, c in enumerate(self.hub.loop.lattice_ids)}
        # turbulence reports Horus can be checked against: FL200+ and a known altitude;
        # the archive repeats some reports, keep one of each
        p = self.hub.pireps
        p = p[(p["tb_max"] >= 4) & (p["fl_hi"] >= g.fl_min) & (p["fl_lo"] < g.fl_max)]
        p = p.drop_duplicates(["time", "lat", "lon", "fl_lo", "fl_hi", "tb_max"])
        self.pireps = p.sort_values("time", kind="stable").reset_index(drop=True)

    @property
    def tau(self) -> float:
        return float(self.hub.loop.params.tau)

    @property
    def normal(self) -> float:
        return float(grid.sigmoid(np.array(self.hub.loop.params.l0)))

    @property
    def clock(self) -> int:
        return int(self.hub.loop.tick)

    def in_band(self, lo: float, hi: float) -> bool:
        if self.fl is None:
            return True
        return hi >= self.fl - 20 and lo <= self.fl + 20

    def update(self) -> list[Line]:
        tick = self.clock
        every = self.w.status_every_s
        if self.pi < 0:  # first call: reports before the window are history, skip them
            self.pi = int(np.searchsorted(self.pireps["time"].to_numpy(), tick, side="right"))
            self.next_status = tick - tick % every + every
        out = self._detections(tick)
        if tick != self.last_tick:
            out += self._forecast(tick)
            out += self._pireps(tick)
            self.last_tick = tick
        out.sort(key=lambda ln: ln.t)
        if tick >= self.next_status:
            out.append(self.status(tick))
            self.next_status += every
        return out

    # ---- aircraft: TURBULENCE when a run of detected minutes starts; calm when a run of
    # 2+ minutes ends (single-minute bumps end silently) ----
    def _detections(self, tick: int) -> list[Line]:
        tau, out = self.tau, []
        fr = self.hub.loop.fresh
        if len(fr):
            fr = fr.sort_values(["minute", "icao24"], kind="stable")
            keep = fr[["minute", "icao24", "lat", "lon", "alt_ft", "T"]]
            self.recent = pd.concat([self.recent, keep], ignore_index=True)
            T = fr["T"].to_numpy(float)
            for r, t_ in zip(fr[T >= tau].itertuples(), T[T >= tau], strict=True):
                fl = int(round(float(r.alt_ft) / 100))
                if not self.in_band(fl, fl):
                    continue
                m, icao = int(r.minute), str(r.icao24)
                h = self.active.get(icao)
                if h is not None and m - h.last <= self.w.calm_after_s:
                    h.last, h.peak = max(h.last, m), max(h.peak, float(t_))
                    continue
                self.active[icao] = Hit(m, m, float(t_))
                self.n_hits += 1
                typ = self.hub.loop.types.get(icao) or "?"
                out.append(
                    Line(
                        m + 60,  # the minute's data is complete at its end
                        "hit",
                        "TURBULENCE",
                        f"{icao}  {typ:<4}  FL{fl:03d}  {t_ / tau:4.1f}× threshold  "
                        f"{self.places.where(float(r.lon), float(r.lat))}",
                    )
                )
        for icao, h in list(self.active.items()):
            if tick - h.last >= self.w.calm_after_s + 60:
                del self.active[icao]
                mins = (h.last - h.start) // 60 + 1
                if mins >= 2:
                    out.append(
                        Line(
                            tick,
                            "calm",
                            "calm",
                            f"{icao}  bumpy for {mins} min "
                            f"({hhmm(h.start)}-{hhmm(h.last + 60)}), peak {h.peak / tau:.1f}×",
                        )
                    )
        if len(self.recent):
            cut = tick - 2 * self.w.pirep_lookback_s
            self.recent = self.recent[self.recent["minute"] >= cut]
        return out

    # ---- forecast: a cell reaching forecast_x times normal (neighbours of a lit cell stay
    # quiet, so a patch drifting with the wind prints once) ----
    def _forecast(self, tick: int) -> list[Line]:
        p = self.hub.loop.probabilities(float(tick))[:, self.bins]
        x = p.max(axis=1) / self.normal
        self.lit = {j for j in self.lit if x[j] >= self.w.rearm_x}
        hot = np.flatnonzero(x >= self.w.forecast_x)
        self.n_hot = len(hot)
        out = []
        horizon = self.hub.cfg.eval.nowcast_horizon_s // 60
        for j in hot[np.argsort(-x[hot])]:
            j = int(j)
            if j in self.lit:
                continue
            cid = h3.int_to_str(int(self.hub.loop.lattice_ids[j]))
            near = (self.index.get(h3.str_to_int(n), -1) for n in h3.grid_disk(cid, 1))
            quiet = any(k in self.lit for k in near)
            self.lit.add(j)
            if quiet:
                continue
            f = int(np.argmax(p[j]))
            lo = int(self.bins_fl[self.bins[f]])
            lat, lon = h3.cell_to_latlng(cid)
            out.append(
                Line(
                    tick,
                    "fc",
                    "FORECAST",
                    f"FL{lo:03d}-{lo + 20:03d}  {self.places.where(lon, lat)}: "
                    f"{p[j, f] * 100:.1f}% chance in the next {horizon} min "
                    f"({x[j]:.0f}× normal)",
                )
            )
        return out

    # ---- pilot reports: at report time, with what Horus had seen in the minutes before ----
    def _pireps(self, tick: int) -> list[Line]:
        out = []
        P = self.pireps
        times = P["time"].to_numpy()
        while self.pi < len(P) and times[self.pi] <= tick:
            r = P.iloc[self.pi]
            self.pi += 1
            lo, hi = float(r["fl_lo"]), float(r["fl_hi"])
            if not self.in_band(lo, hi):
                continue
            self.n_reports += 1
            tp, lat, lon, lvl = int(r["time"]), float(r["lat"]), float(r["lon"]), int(r["tb_max"])
            passed, felt = self._near(tp, lat, lon, lo, hi)
            self.n_covered += passed > 0
            self.n_backed += felt > 0
            fx = self._forecast_at(lat, lon, lo, hi, tick)
            km = f"{self.w.pirep_near_km:g} km"
            if not passed:
                seen = f"no aircraft passed within {km} just before"
            elif not felt:
                seen = f"{plural(passed, 'aircraft')} passed within {km} just before, none felt it"
            else:
                seen = (
                    f"{plural(passed, 'aircraft')} passed within {km} just before, {felt} felt it"
                )
            fc = "forecast normal" if fx < self.w.rearm_x else f"forecast {fx:.0f}× normal"
            typ = r.get("aircraft")
            who = f" ({typ})" if isinstance(typ, str) and typ else ""
            out.append(
                Line(
                    tp,
                    "pirep_sev" if lvl >= 6 else "pirep",
                    "PILOT REPORT",
                    f"{LEVEL.get(lvl, 'moderate+')} at {fl_range(lo, hi)} "
                    f"{self.places.where(lon, lat)}{who}.  Horus: {seen}; {fc}",
                )
            )
        return out

    def _near(self, tp: int, lat: float, lon: float, lo: float, hi: float) -> tuple[int, int]:
        """Aircraft (settled minutes) within the match radius and 2,000 ft of the report in
        the lookback before it: how many passed, how many of those detected."""
        r = self.recent
        if r.empty:
            return 0, 0
        fl = r["alt_ft"].to_numpy(float) / 100
        m = r["minute"].to_numpy()
        ok = (m >= tp - self.w.pirep_lookback_s) & (m <= tp) & (fl >= lo - 20) & (fl <= hi + 20)
        if not ok.any():
            return 0, 0
        d = geo.haversine_km(r["lat"].to_numpy()[ok], r["lon"].to_numpy()[ok], lat, lon)
        near = r[ok][d <= self.w.pirep_near_km]
        hit = near["T"].to_numpy(float) >= self.tau
        return int(near["icao24"].nunique()), int(near["icao24"][hit].nunique())

    def _forecast_at(self, lat: float, lon: float, lo: float, hi: float, tick: int) -> float:
        g = self.hub.cfg.grid
        ids = passes.cell_ids(np.array([lat]), np.array([lon]), g.h3_res)
        j = int(grid.cell_index(ids, self.hub.loop.lattice_ids)[0])
        if j < 0:
            return 0.0
        p = self.hub.loop.probabilities(float(tick))[j]
        b = geo.fl_bin(
            np.array([max(lo, g.fl_min) * 100, min(hi, g.fl_max - 1) * 100]),
            g.fl_min,
            g.fl_max,
            g.fl_bin,
        )
        return float(p[int(b.min()) : int(b.max()) + 1].max()) / self.normal

    def status(self, tick: int) -> Line:
        n_ac = sum(self.in_band(a["fl"], a["fl"]) for a in self.hub.aircraft())
        bumpy = sum(tick - h.last <= 120 for h in self.active.values())
        return Line(
            tick,
            "status",
            "·",
            f"{n_ac:,} aircraft above FL200 · {bumpy} in turbulence · "
            f"{plural(self.n_hot, 'forecast cell')} above {self.w.forecast_x:g}× normal",
        )


def run_live(
    hub: Any,
    out: Printer,
    w: Watch,
    places: Places,
    speed: float,
    fl: int | None = None,
    minutes: float | None = None,
    loop: bool = False,
) -> Watcher:
    feed = hub.feed
    snap = feed.snapshot_s
    t0 = feed.t_first
    day = time.strftime("%Y-%m-%d", time.gmtime(t0))
    pace = (
        "as fast as this machine runs"
        if math.isinf(speed)
        else f"at {speed:g}x real time (1 s here = {speed:g} s of flights)"
    )
    settle = (hub.loop.settle_s + 60) // 60
    out.raw("HORUS  turbulence from aircraft vertical speed", "head")
    out.raw(f"Replaying {day} {hhmm(t0)}-{hhmm(feed.t_last)} from the archive, {pace}.")
    out.raw("Real flights, run through the same code a live feed uses. Ctrl-C stops.")
    if fl is not None:
        out.raw(f"Showing only FL{fl - 20:03d}-{fl + 20:03d}.")
    out.raw("Research prototype. Not for flight decisions.", "status")
    out.raw()
    out.raw(LEGEND.format(h=hub.cfg.eval.nowcast_horizon_s // 60), "status")
    out.raw(
        f"Lines run about {settle} min behind the flights: each minute of data settles "
        "before it counts.",
        "status",
    )
    out.raw()
    watcher = Watcher(hub, places, w, fl)
    now = t0
    hub.now = t0
    try:
        while True:
            now += snap
            if now > feed.t_last + snap:
                summary(out, watcher, t0, watcher.clock)
                if not loop:
                    return watcher
                out.raw("\n-- window finished, starting it again --\n", "status")
                hub.restart()
                watcher = Watcher(hub, places, w, fl)
                now = t0
                continue
            t_wall = time.perf_counter()
            hub.step(now)
            for ln in watcher.update():
                out.line(ln)
            if minutes is not None and now - t0 >= minutes * 60:
                break
            if not math.isinf(speed):
                time.sleep(max(snap / speed - (time.perf_counter() - t_wall), 0.0))
    except KeyboardInterrupt:
        pass
    summary(out, watcher, t0, watcher.clock)
    return watcher


def summary(out: Printer, wt: Watcher, t0: float, t1: float) -> None:
    out.raw()
    out.raw(f"Summary {hhmm(t0)}-{hhmm(t1)}", "head")
    out.raw(f"  {plural(wt.n_hits, 'turbulence detection')} (runs of bumpy minutes)")
    out.raw(
        f"  {plural(wt.n_reports, 'pilot report')} of moderate or worse at FL200+: "
        f"{wt.n_covered} had aircraft passing within {wt.w.pirep_near_km:g} km and 2,000 ft in the "
        f"{wt.w.pirep_lookback_s // 60} min before, and {wt.n_backed} of those had a detection"
    )


def run_case(
    case: dict[str, Any],
    k: int,
    n: int,
    tau: float,
    out: Printer,
    w: Watch,
    places: Places,
    pace_s: float,
    h3_res: int,
) -> None:
    m, frames = case["meta"], case["frames"]
    normal = float(m["floor"]) / 2
    lo, hi = float(m["fl_lo"]), float(m["fl_hi"])
    word = LEVEL_WORD.get(m["intensity"], m["intensity"].lower())
    when = time.strftime("%a %d %b %Y, %H:%MZ", time.gmtime(m["time"]))
    out.raw(
        f"CASE {k} of {n}  ·  {when}  ·  {word} turbulence reported at {fl_range(lo, hi)} "
        f"{places.where(m['lon'], m['lat'])}",
        "head",
    )
    out.raw("Picked as the clearest detection on its test day, not at random.", "status")
    span = round((frames[-1]["t"] - frames[0]["t"]) / 120)
    out.raw(f"{span} min either side of the report, one line per change.", "status")
    out.raw()
    rep_cell = h3.latlng_to_cell(m["lat"], m["lon"], h3_res)

    def dist(lat: float, lon: float) -> float:
        return float(geo.haversine_km(lat, lon, m["lat"], m["lon"]))

    seen: set[str] = set()
    near: set[str] = set()
    reported, lit_prev = False, 0
    for i, f in enumerate(frames):
        t = float(f["t"])
        rel = round((t - m["time"]) / 60)
        stamp = f"{rel:+d} min" if rel else "0 min"
        lines: list[Line] = []
        ac = [a for a in f["aircraft"] if dist(a[1], a[2]) <= w.case_radius_km]
        hits = [a for a in ac if a[4] is not None and a[4] >= tau]
        for icao, lat, lon, fl, T in hits:
            if icao in seen:
                continue
            seen.add(icao)
            d = dist(lat, lon)
            if d <= w.pirep_near_km and lo - 20 <= fl <= hi + 20:
                near.add(icao)
            lines.append(
                Line(
                    t,
                    "hit",
                    "TURBULENCE",
                    f"{icao}  FL{fl:03d}  {T / tau:4.1f}× threshold  {d:.0f} km from the report",
                )
            )
        if not reported and t >= m["time"]:
            reported = True
            px = dict(f["cells"]).get(rep_cell, normal) / normal
            fc = (
                "forecast at that spot: normal"
                if px < w.rearm_x
                else f"forecast at that spot: {px:.0f}× normal"
            )
            lines.append(
                Line(
                    t,
                    "pirep_sev" if m["intensity"] != "MOD" else "pirep",
                    "PILOT REPORT",
                    f"{word} at {fl_range(lo, hi)}   Horus so far: {len(near)} aircraft felt it "
                    f"within {w.pirep_near_km:g} km, {fc}",
                )
            )
        hot = sorted(
            (
                (p / normal, dist(*h3.cell_to_latlng(c)))
                for c, p in f["cells"]
                if p / normal >= w.forecast_x
            ),
            reverse=True,
        )
        hot = [x for x in hot if x[1] <= w.case_radius_km]
        if hot and not lit_prev:
            x, d = hot[0]
            more = f" (+{len(hot) - 1} more)" if len(hot) > 1 else ""
            lines.append(
                Line(
                    t,
                    "fc",
                    "FORECAST",
                    f"cell {d:.0f} km from the report at {x:.0f}× normal{more}; "
                    "it drifts with the wind",
                )
            )
        elif lit_prev and not hot:
            lines.append(Line(t, "calm", "forecast", "faded back to normal"))
        lit_prev = len(hot)
        if i % 10 == 0:
            lines.insert(
                0,
                Line(
                    t,
                    "status",
                    "·",
                    f"{len(ac)} aircraft within {w.case_radius_km:g} km · "
                    f"{len(hits)} in turbulence · {plural(len(hot), 'forecast cell')} lit",
                ),
            )
        for ln in lines:
            out.line(ln, stamp)
        if pace_s:
            time.sleep(pace_s)
    out.raw()
    out.raw(
        f"  {len(seen)} aircraft hit turbulence within {w.case_radius_km:g} km; {len(near)} of "
        f"them within "
        f"{w.pirep_near_km:g} km and 2,000 ft of the report.",
        "head",
    )
    out.raw()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="horus.watch", description=__doc__.split("\n\n")[0])
    ap.add_argument("--fl", type=int, help="only events within 2,000 ft of this level, e.g. 340")
    ap.add_argument("--speed", type=float, help="replay speed, x real time (config default)")
    ap.add_argument("--fast", action="store_true", help="no waiting between steps")
    ap.add_argument("--minutes", type=float, help="stop after this many simulated minutes")
    ap.add_argument("--loop", action="store_true", help="start the window again when it ends")
    ap.add_argument("--day", help="archive day (default: first test day)")
    ap.add_argument("--hour", type=int, default=15, help="window start hour, UTC")
    ap.add_argument("--hours", type=float, default=3.0, help="window length")
    ap.add_argument("--case", type=int, help="tell one case study (1, 2 or 3)")
    ap.add_argument("--cases", action="store_true", help="tell all case studies")
    ap.add_argument("--no-color", action="store_true")
    a = ap.parse_args(argv)
    cfg = config.load()
    w = cfg.watch
    colour = sys.stdout.isatty() and not a.no_color and "NO_COLOR" not in os.environ
    out = Printer(sys.stdout, colour)
    places = Places()
    if a.case is not None or a.cases:
        from horus.eval import nowcast_bench as nb

        hl = json.loads(config.resolve("models/half_life.json").read_text())
        tau = nb.grid_params(cfg, hl)[0].tau
        files = sorted(config.resolve(w.cases_dir).glob("*.json"))
        if not files:
            out.raw("No case studies found: run `make replays` first.")
            return 1
        picks = range(1, len(files) + 1) if a.cases else [a.case]
        for k in picks:
            if not 1 <= k <= len(files):
                out.raw(f"--case must be 1..{len(files)}")
                return 1
            case = json.loads(files[k - 1].read_text())
            pace = 0.0 if a.fast else w.case_pace_s
            run_case(case, k, len(files), tau, out, w, places, pace, cfg.grid.h3_res)
        return 0
    from horus.api.main import build_hub

    out.raw("Loading a day of flights (about 30 s)...", "status")
    hub = build_hub(cfg, a.day, a.hour, a.hours)
    speed = math.inf if a.fast else (a.speed or w.speed)
    run_live(hub, out, w, places, speed, a.fl, a.minutes, a.loop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
