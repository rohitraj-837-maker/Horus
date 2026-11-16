"""Horus in the terminal: a live dashboard over the same live loop as the API.

    make tui            (or: uv run python -m horus.tui)

No server and no browser: the archive feed, live loop, grid and winds run
in-process (horus.api.main.build_hub). Live mode replays a real test day at 30x;
Cases mode replays the curated reports (scripts/make_replays.py).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from matplotlib.path import Path as MplPath
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from horus import config
from horus.core import grid
from horus.core.winds import wind_at
from horus.eval import passes
from horus.tui.mapview import CONUS, Layers, View, outline_mask, render

AMBER, SEV, DIM, INK = "#ffb02e", "#ff5040", "#7f8a9c", "#eef1f6"


def zulu(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(t))


class States:
    """State outlines (Natural Earth, bundled) and point -> state code."""

    def __init__(self) -> None:
        data = json.loads(config.resolve("demo/data/conus_states.json").read_text())
        self.rings: list[np.ndarray] = []
        self.paths: list[tuple[str, MplPath]] = []
        for st in data["states"]:
            for r in st["rings"]:
                a = np.asarray(r, dtype=float)
                self.rings.append(a)
                self.paths.append((st["code"], MplPath(a)))

    def at(self, lon: float, lat: float) -> str:
        for code, p in self.paths:
            if p.contains_point((lon, lat)):
                return code
        return "offshore"


HELP = f"""[b]What you are looking at[/b]

[b]Background colour[/b]   wind at the chosen altitude: dark blue = calm,
                    brighter blue = the jet stream.
[on #a8620e]   [/]  [b]amber cells[/b]   the 15-minute forecast: chance the next aircraft
                through that ~26 km patch meets turbulence (brighter = higher).
[{INK}]⠂[/]  [b]dots[/b]          aircraft at this altitude.
[bold {AMBER}]⣿[/]  [b]amber block[/b]   an aircraft whose own vertical-speed data shows
                turbulence now.
[bold {AMBER}]^[/] [bold {SEV}]^[/]  [b]carets[/b]        pilot reports: moderate / severe.
                Used to check Horus, never to feed it.

[b]Detecting now[/b]   aircraft currently over the threshold, with how far over it they are.
[b]Highest forecast[/b] the strongest cells at this altitude and where they are.
[b]Live[/b] replays a real archived day (2026-01-01) at 30x through the same code a live feed
would use. [b]Cases[/b] replays three real turbulence reports minute by minute.

[b]Keys[/b]  ↑ ↓ altitude   + - zoom   w a s d pan   0 reset view
      c cases (next)   l live   space pause   ← → step   ? this help   q quit

[{DIM}]Research prototype. Not for flight decisions.  Press any key to close.[/]"""


class HelpScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    HelpScreen { align: center middle; background: rgba(0,0,0,0.6); }
    #help { width: 96; height: auto; padding: 1 2; border: round #3a4458; background: #0b0f17; }
    """

    def compose(self) -> ComposeResult:
        yield Static(HELP, id="help")

    def on_key(self) -> None:
        self.dismiss()


@dataclass
class Live:
    """Live-mode bookkeeping for the log."""

    detecting: set[str] = field(default_factory=set)
    logged_cells: set[int] = field(default_factory=set)
    log: list[tuple[float, str, str]] = field(default_factory=list)


class HorusTUI(App[None]):
    TITLE = "Horus"
    CSS = """
    Screen { background: #03060d; }
    #top { height: 1; padding: 0 1; background: #0b111c; color: #eef1f6; }
    #body { height: 1fr; }
    #map { width: 1fr; height: 1fr; }
    #side { width: 44; padding: 0 1; border-left: tall #1a2232; }
    #det { height: auto; max-height: 12; }
    #fc { height: auto; max-height: 9; margin-top: 1; }
    #log { height: 1fr; margin-top: 1; }
    #bar { height: 1; padding: 0 1; background: #0b111c; color: #a7b0bf; }
    """
    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("up", "fl(1)", "altitude up", show=False),
        Binding("down", "fl(-1)", "altitude down", show=False),
        Binding("plus,equals_sign", "zoom(1.4)", "zoom in", show=False),
        Binding("minus", "zoom(0.714)", "zoom out", show=False),
        Binding("w", "pan(0,0.15)", show=False),
        Binding("s", "pan(0,-0.15)", show=False),
        Binding("a", "pan(-0.15,0)", show=False),
        Binding("d", "pan(0.15,0)", show=False),
        Binding("0", "reset_view", show=False),
        Binding("c", "next_case", "cases"),
        Binding("l", "live", "live"),
        Binding("space", "pause", show=False),
        Binding("left", "step(-1)", show=False),
        Binding("right", "step(1)", show=False),
        Binding("question_mark", "help", "help"),
    ]

    def __init__(self, hub: Any | None = None, replays_dir: str = "data/replays") -> None:
        super().__init__()
        self.hub = hub
        self.cfg = config.load()
        self.states = States()
        g = self.cfg.grid
        self.fl_bins = list(range(g.fl_min, g.fl_max, g.fl_bin))
        self.fl = self.fl_bins.index(340)
        self.mode = "live"
        self.view: View | None = None
        self.size_key: tuple[Any, ...] = ()
        self.cache: dict[str, Any] = {}
        self.live = Live()
        self.cases = [
            json.loads(p.read_text()) for p in sorted(config.resolve(replays_dir).glob("*.json"))
        ]
        self.case_i = -1
        self.frame = 0
        self.playing = True
        self.started = False

    # ---------- layout ----------
    def compose(self) -> ComposeResult:
        yield Static("", id="top")
        with Horizontal(id="body"):
            yield Static("Loading a day of flights (about 20 s)…", id="map")
            with Vertical(id="side"):
                yield Static("", id="det")
                yield Static("", id="fc")
                yield Static("", id="log")
        yield Static("", id="bar")

    def on_mount(self) -> None:
        if self.hub is None:
            self.run_worker(self.load_hub, thread=True, exclusive=True)
        else:
            self.start_feed()
        self.set_interval(0.5, self.tick)
        self.query_one("#bar", Static).update(self.keys_line())

    def load_hub(self) -> None:
        from horus.api.main import build_hub

        hub = build_hub(self.cfg)
        self.call_from_thread(self.hub_loaded, hub)

    def hub_loaded(self, hub: Any) -> None:
        self.hub = hub
        self.start_feed()

    def start_feed(self) -> None:
        self.started = True
        self.hub.now = self.hub.feed.t_first if self.hub.feed is not None else self.hub.now
        self.run_worker(self.hub.run(), exclusive=False)

    # ---------- geometry caches ----------
    def map_size(self) -> tuple[int, int]:
        m = self.query_one("#map", Static)
        return max(10, m.size.width), max(5, m.size.height)

    def ensure_view(self, cols: int, rows: int) -> View:
        key = (cols, rows, self.mode, self.case_i)
        if self.view is None or key != self.size_key:
            self.size_key = key
            if self.mode == "case" and self.case_i >= 0:
                m = self.cases[self.case_i]["meta"]
                self.view = View.fit(
                    cols, rows, m["lon"] - 5, m["lon"] + 5, m["lat"] - 3.2, m["lat"] + 3.2
                )
            else:
                self.view = View.fit(cols, rows, *CONUS)
            self.cache.clear()
        return self.view

    def cached(self, name: str, key: tuple[Any, ...], make: Any) -> Any:
        hit = self.cache.get(name)
        if hit is not None and hit[0] == key:
            return hit[1]
        val = make()
        self.cache[name] = (key, val)
        return val

    def vkey(self) -> tuple[Any, ...]:
        v = self.view
        return (
            round(v.lon0, 4),
            round(v.lon1, 4),
            round(v.lat0, 4),
            round(v.lat1, 4),
            self.size_key,
        )

    def char_cells(self, cols: int, rows: int) -> np.ndarray:
        def make() -> np.ndarray:
            lon, lat = self.view.char_centres(cols, rows)
            ids = passes.cell_ids(lat.ravel(), lon.ravel(), self.cfg.grid.h3_res)
            return grid.cell_index(ids, self.hub.loop.lattice_ids).reshape(rows, cols)

        return self.cached("cells", self.vkey(), make)

    def wind_speed(self, cols: int, rows: int, t: float) -> np.ndarray | None:
        w = getattr(self.hub, "winds", None)
        if w is None:
            return None
        hour = int(t // 3600)
        fl = self.fl_bins[self.fl] + 10

        def make() -> np.ndarray:
            lon, lat = self.view.char_centres(cols, rows)
            n = lon.size
            u, v = wind_at(
                w,
                np.full(n, hour * 3600 + 1800.0),
                lat.ravel(),
                lon.ravel(),
                np.full(n, fl * 100.0),
            )
            sp = np.hypot(u, v).reshape(rows, cols)
            inside = (
                (lon >= CONUS[0]) & (lon <= CONUS[1] - 1) & (lat >= CONUS[2]) & (lat <= CONUS[3])
            )
            return np.where(inside, sp, np.nan)

        return self.cached("wind", (*self.vkey(), hour, fl), make)

    # ---------- main loop ----------
    def tick(self) -> None:
        if not self.started:
            return
        cols, rows = self.map_size()
        view = self.ensure_view(cols, rows)
        outl = self.cached(
            "outline", self.vkey(), lambda: outline_mask(view, cols, rows, self.states.rings)
        )
        floor = float(grid.sigmoid(np.array(self.hub.loop.params.l0))) * 2
        tau = float(self.hub.loop.params.tau)
        L = Layers(floor=floor, outline_mask=outl)
        if self.mode == "case" and self.case_i >= 0:
            t, narration = self.case_layers(L, cols, rows)
        else:
            t, narration = self.live_layers(L, cols, rows, floor, tau)
        lines = render(view, cols, rows, L)
        txt = Text()
        for i, runs in enumerate(lines):
            for s, style in runs:
                txt.append(s, style=style)
            if i < len(lines) - 1:
                txt.append("\n")
        self.query_one("#map", Static).update(txt)
        fl = self.fl_bins[self.fl]
        mode = (
            "LIVE · archived day at 30×"
            if self.mode == "live"
            else f"CASE {self.case_i + 1}/{len(self.cases)}"
        )
        top = Text.assemble(
            ("HORUS", "bold"),
            "  ",
            (zulu(t), "bold"),
            "  ",
            (f"FL{fl}", f"bold {AMBER}"),
            f" ({fl * 100:,} ft)   ",
            (mode, DIM),
        )
        self.query_one("#top", Static).update(top)
        self.query_one("#bar", Static).update(narration or self.keys_line())

    def live_layers(
        self, L: Layers, cols: int, rows: int, floor: float, tau: float
    ) -> tuple[float, Text | None]:
        hub, t = self.hub, float(self.hub.now)
        fl = self.fl_bins[self.fl]
        L.wind_speed = self.wind_speed(cols, rows, t)
        p_all = hub.loop.probabilities(t)
        p = p_all[:, self.fl]
        idx = self.char_cells(cols, rows)
        L.turb_p = np.where(idx >= 0, p[np.maximum(idx, 0)], np.nan)
        ac = [a for a in hub.aircraft() if fl - 10 <= a["fl"] < fl + 30]
        if ac:
            L.aircraft = np.array([[a["lon"], a["lat"]] for a in ac])
            L.detecting = np.array([a["T"] is not None and a["T"] >= tau for a in ac])
        pir = hub.pireps
        recent = pir[
            (pir["time"] <= t)
            & (pir["time"] > t - 7200)
            & (pir["tb_max"] >= 4)
            & (pir["fl_hi"] >= fl - 20)
            & (pir["fl_lo"] <= fl + 40)
        ]
        L.pireps = [(float(r.lon), float(r.lat), bool(r.tb_max >= 6)) for r in recent.itertuples()]
        self.update_side_live(ac, p_all, t, tau, floor)
        return t, None

    def case_layers(self, L: Layers, cols: int, rows: int) -> tuple[float, Text]:
        case = self.cases[self.case_i]
        frames, m = case["frames"], case["meta"]
        if self.playing:
            self.frame = min(self.frame + 1, len(frames) - 1)
        f = frames[self.frame]
        t = float(f["t"])
        L.wind_speed = self.wind_speed(cols, rows, t)
        idx = self.char_cells(cols, rows)
        p = np.full(len(self.hub.loop.lattice_ids), np.nan)
        for h, pv in f["cells"]:
            j = grid.cell_index(np.array([int(h, 16)], dtype=np.int64), self.hub.loop.lattice_ids)[
                0
            ]
            if j >= 0:
                p[j] = pv
        L.turb_p = np.where(idx >= 0, p[np.maximum(idx, 0)], np.nan)
        tau = float(self.hub.loop.params.tau)
        ac = f["aircraft"]
        if ac:
            L.aircraft = np.array([[a[2], a[1]] for a in ac])
            L.detecting = np.array([a[4] is not None and a[4] >= tau for a in ac])
        L.pireps = [(m["lon"], m["lat"], m["intensity"] == "SEV")]
        self.update_side_case(case, f, tau)
        mins = round((t - m["time"]) / 60)
        when = (
            "report time"
            if mins == 0
            else f"{abs(mins)} min {'before' if mins < 0 else 'after'} the report"
        )
        near_hit = any(
            a[4] is not None
            and a[4] >= tau
            and abs(a[1] - m["lat"]) < 0.9
            and abs(a[2] - m["lon"]) < 1.1
            for a in ac
        )
        if abs(t - m["time"]) < 90:
            word = "severe" if m["intensity"] == "SEV" else "moderate"
            what = f"a pilot reports {word} turbulence at FL{m['fl_lo']}"
        elif t < m["time"]:
            what = (
                "an aircraft nearby already feels it"
                if near_hit
                else "traffic crossing, nothing detected yet"
            )
        else:
            what = (
                f"forecast lit: {len(f['cells'])} cell(s), drifting with the wind"
                if f["cells"]
                else "no forecast cell here yet"
            )
        bar = Text.assemble(
            ("▶ " if self.playing else "❚❚ ", AMBER),
            (when, "bold"),
            "  ·  ",
            what,
            ("    space pause  ← → step  c next  l live", DIM),
        )
        return t, bar

    # ---------- side panels ----------
    def update_side_live(
        self, ac: list[dict[str, Any]], p_all: np.ndarray, t: float, tau: float, floor: float
    ) -> None:
        hits = sorted(
            (a for a in ac if a["T"] is not None and a["T"] >= tau), key=lambda a: -a["T"]
        )
        tb = Table(
            title="DETECTING NOW",
            title_justify="left",
            title_style=f"bold {AMBER}",
            box=None,
            padding=(0, 1),
            expand=True,
            show_header=True,
            header_style=DIM,
        )
        for col in ("aircraft", "FL", "where", "strength"):
            tb.add_column(col)
        for a in hits[:8]:
            tb.add_row(
                a["icao24"],
                str(a["fl"]),
                self.states.at(a["lon"], a["lat"]),
                f"{a['T'] / tau:.1f}× thr",
            )
        self.query_one("#det", Static).update(
            tb
            if hits
            else Text.assemble(
                ("DETECTING NOW\n", f"bold {AMBER}"), ("none at this altitude right now", DIM)
            )
        )

        p = p_all[:, self.fl]
        top = np.argsort(p)[::-1][:5]
        ft = Table(
            title="HIGHEST FORECAST (next 15 min)",
            title_justify="left",
            title_style=f"bold {AMBER}",
            box=None,
            padding=(0, 1),
            expand=True,
            header_style=DIM,
        )
        for col in ("chance", "vs normal", "near"):
            ft.add_column(col)
        for j in top:
            if p[j] <= floor:
                break
            la, lo = self._cell_ll(int(j))
            ft.add_row(f"{p[j] * 100:.1f}%", f"{p[j] / (floor / 2):.0f}×", self.states.at(lo, la))
        empty = not len(top) or p[top[0]] <= floor
        self.query_one("#fc", Static).update(
            ft
            if not empty
            else Text.assemble(
                ("HIGHEST FORECAST (next 15 min)\n", f"bold {AMBER}"),
                ("nothing above normal at this altitude", DIM),
            )
        )

        now_hits = {a["icao24"] for a in hits}
        for a in hits:
            if a["icao24"] not in self.live.detecting:
                self.live.log.insert(
                    0,
                    (
                        t,
                        "hit",
                        f"{a['icao24']} FL{a['fl']} "
                        f"{self.states.at(a['lon'], a['lat'])} turbulence",
                    ),
                )
        self.live.detecting = now_hits
        for j in np.flatnonzero(p_all.max(axis=1) >= 0.03):
            if int(j) not in self.live.logged_cells:
                self.live.logged_cells.add(int(j))
                f = int(np.argmax(p_all[j]))
                la, lo = self._cell_ll(int(j))
                self.live.log.insert(
                    0,
                    (
                        t,
                        "cell",
                        f"forecast {p_all[j, f] * 100:.0f}% FL{self.fl_bins[f]} "
                        f"{self.states.at(lo, la)}",
                    ),
                )
        self.live.log = self.live.log[:40]
        self.show_log(self.live.log)

    def update_side_case(self, case: dict[str, Any], f: dict[str, Any], tau: float) -> None:
        m = case["meta"]
        tb = Table(
            title=f"CASE · {m['date']} · {m['intensity']} at FL{m['fl_lo']}",
            title_justify="left",
            title_style=f"bold {AMBER}",
            box=None,
            padding=(0, 1),
            expand=True,
            header_style=DIM,
        )
        for col in ("aircraft", "FL", "where", "strength"):
            tb.add_column(col)
        hits = sorted(
            (a for a in f["aircraft"] if a[4] is not None and a[4] >= tau), key=lambda a: -a[4]
        )
        for a in hits[:8]:
            tb.add_row(a[0], str(a[3]), self.states.at(a[2], a[1]), f"{a[4] / tau:.1f}× thr")
        title = f"CASE · {m['date']} · {m['intensity']} at FL{m['fl_lo']}\n"
        self.query_one("#det", Static).update(
            tb
            if hits
            else Text.assemble((title, f"bold {AMBER}"), ("no aircraft detecting this minute", DIM))
        )
        self.query_one("#fc", Static).update(
            Text.assemble(
                ("SELECTED AS ", DIM),
                ("the clearest detections in the test days, not at random", DIM),
            )
        )
        log: list[tuple[float, str, str]] = []
        seen: set[str] = set()
        for fr in case["frames"][: self.frame + 1]:
            for a in fr["aircraft"]:
                if a[4] is not None and a[4] >= tau and a[0] not in seen:
                    seen.add(a[0])
                    log.insert(
                        0,
                        (
                            fr["t"],
                            "hit",
                            f"{a[0]} FL{a[3]} {self.states.at(a[2], a[1])} turbulence",
                        ),
                    )
            if abs(fr["t"] - m["time"]) < 31:
                log.insert(
                    0,
                    (
                        m["time"],
                        "pirep",
                        f"PIREP {m['intensity'].lower()} FL{m['fl_lo']}",
                    ),
                )
        self.show_log(log)

    def show_log(self, log: list[tuple[float, str, str]]) -> None:
        txt = Text("LOG\n", style=f"bold {AMBER}")
        if not log:
            txt.append("detections and rising forecasts appear here", style=DIM)
        for t, kind, msg in log[:30]:
            style = AMBER if kind == "hit" else SEV if kind == "pirep" else INK
            txt.append(time.strftime("%H:%MZ ", time.gmtime(t)), style=DIM)
            txt.append(msg + "\n", style=style)
        self.query_one("#log", Static).update(txt)

    def _cell_ll(self, j: int) -> tuple[float, float]:
        import h3

        return h3.cell_to_latlng(h3.int_to_str(int(self.hub.loop.lattice_ids[j])))

    def keys_line(self) -> Text:
        return Text(
            "↑↓ altitude   +/- zoom   wasd pan   0 reset   c cases   l live   ? help   q quit",
            style=DIM,
        )

    # ---------- actions ----------
    def action_fl(self, d: int) -> None:
        self.fl = max(0, min(len(self.fl_bins) - 1, self.fl + d))

    def action_zoom(self, f: float) -> None:
        if self.view is not None:
            self.view = self.view.zoom(f)
            self.cache.clear()

    def action_pan(self, dx: float, dy: float) -> None:
        if self.view is not None:
            self.view = self.view.pan(dx, dy)
            self.cache.clear()

    def action_reset_view(self) -> None:
        self.view = None

    def action_next_case(self) -> None:
        if not self.cases:
            self.notify("No case studies found: run `make replays`.", severity="warning")
            return
        self.mode = "case"
        self.case_i = (self.case_i + 1) % len(self.cases)
        c = self.cases[self.case_i]
        rep = next((i for i, f in enumerate(c["frames"]) if f["t"] >= c["meta"]["time"]), 0)
        self.frame = max(0, rep - 10)
        self.playing = True
        m = c["meta"]
        self.fl = next((i for i, b in enumerate(self.fl_bins) if b <= m["fl_lo"] < b + 20), self.fl)
        self.view = None

    def action_live(self) -> None:
        self.mode = "live"
        self.case_i = -1
        self.view = None

    def action_pause(self) -> None:
        self.playing = not self.playing

    def action_step(self, d: int) -> None:
        if self.mode == "case":
            self.playing = False
            self.frame = max(0, min(len(self.cases[self.case_i]["frames"]) - 1, self.frame + d))

    def action_help(self) -> None:
        self.push_screen(HelpScreen())


def main() -> None:
    HorusTUI().run()
