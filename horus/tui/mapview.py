"""Text map for the terminal: braille dots over coloured character cells.

Each character cell is 2x4 braille dots, so a terminal gives ~4x its character
resolution, and dots come out roughly square (cells are ~1:2). Layers, bottom up:
    background colour  wind speed at the selected flight level (blue ramp)
                       or the turbulence forecast where a cell is lit (amber ramp)
    braille dots       state outlines (dim), aircraft (white / amber if detecting)
    glyph              '^' pilot reports (orange moderate, red severe)
Pure: inputs are arrays and the state polygons; output is a list of styled runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

COSLAT = float(np.cos(np.radians(37.0)))
CONUS = (-125.0, -66.0, 24.0, 50.0)  # lon0, lon1, lat0, lat1
BRAILLE_BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))  # [row][col]

WIND_STOPS = [
    (0, (5, 10, 22)),
    (20, (10, 30, 62)),
    (40, (16, 58, 98)),
    (60, (24, 92, 130)),
    (85, (40, 128, 160)),
]
TURB_STOPS = [(0.0, (70, 40, 8)), (0.5, (150, 88, 14)), (1.0, (230, 150, 40))]


def _ramp(stops: list[tuple[float, tuple[int, int, int]]], x: np.ndarray) -> np.ndarray:
    xs = np.array([s[0] for s in stops], dtype=float)
    cs = np.array([s[1] for s in stops], dtype=float)
    out = np.stack([np.interp(x, xs, cs[:, k]) for k in range(3)], axis=-1)
    return out.astype(np.uint8)


@dataclass
class View:
    """Lon/lat window shown in a cols x rows character area."""

    lon0: float
    lon1: float
    lat0: float
    lat1: float

    @classmethod
    def fit(
        cls,
        cols: int,
        rows: int,
        lon0: float = CONUS[0],
        lon1: float = CONUS[1],
        lat0: float = CONUS[2],
        lat1: float = CONUS[3],
    ) -> View:
        """Smallest window containing the box with the terminal's aspect (square dots)."""
        w_dots, h_dots = 2 * cols, 4 * rows
        cx, cy = (lon0 + lon1) / 2, (lat0 + lat1) / 2
        span_x = (lon1 - lon0) * COSLAT
        span_y = lat1 - lat0
        k = min(w_dots / span_x, h_dots / span_y)  # dots per degree latitude
        half_x = w_dots / k / COSLAT / 2
        half_y = h_dots / k / 2
        return cls(cx - half_x, cx + half_x, cy - half_y, cy + half_y)

    def zoom(self, f: float) -> View:
        cx, cy = (self.lon0 + self.lon1) / 2, (self.lat0 + self.lat1) / 2
        hx, hy = (self.lon1 - self.lon0) / 2 / f, (self.lat1 - self.lat0) / 2 / f
        return View(cx - hx, cx + hx, cy - hy, cy + hy)

    def pan(self, dx: float, dy: float) -> View:
        """Move by a fraction of the window."""
        wx, wy = (self.lon1 - self.lon0) * dx, (self.lat1 - self.lat0) * dy
        return View(self.lon0 + wx, self.lon1 + wx, self.lat0 + wy, self.lat1 + wy)

    def to_dots(
        self, lon: np.ndarray, lat: np.ndarray, cols: int, rows: int
    ) -> tuple[np.ndarray, np.ndarray]:
        x = (np.asarray(lon, float) - self.lon0) / (self.lon1 - self.lon0) * (2 * cols)
        y = (self.lat1 - np.asarray(lat, float)) / (self.lat1 - self.lat0) * (4 * rows)
        return np.floor(x).astype(int), np.floor(y).astype(int)

    def char_centres(self, cols: int, rows: int) -> tuple[np.ndarray, np.ndarray]:
        lon = self.lon0 + (np.arange(cols) + 0.5) / cols * (self.lon1 - self.lon0)
        lat = self.lat1 - (np.arange(rows) + 0.5) / rows * (self.lat1 - self.lat0)
        return np.meshgrid(lon, lat)


@dataclass
class Layers:
    wind_speed: np.ndarray | None = None  # (rows, cols) m/s at char centres, NaN outside
    turb_p: np.ndarray | None = None  # (rows, cols) forecast probability, NaN where unlit
    floor: float = 0.001
    outline: list[np.ndarray] = field(default_factory=list)  # rings of (lon, lat)
    outline_mask: np.ndarray | None = None  # precomputed outline_mask(...) for this view
    aircraft: np.ndarray | None = None  # (n, 2) lon, lat
    detecting: np.ndarray | None = None  # (n,) bool
    pireps: list[tuple[float, float, bool]] = field(default_factory=list)  # lon, lat, severe


def _line_dots(x0: int, y0: int, x1: int, y1: int) -> tuple[np.ndarray, np.ndarray]:
    n = max(abs(x1 - x0), abs(y1 - y0), 1)
    t = np.linspace(0.0, 1.0, n + 1)
    return np.round(x0 + (x1 - x0) * t).astype(int), np.round(y0 + (y1 - y0) * t).astype(int)


def outline_mask(view: View, cols: int, rows: int, rings: list[np.ndarray]) -> np.ndarray:
    """Dot raster of polygon outlines (cache it per view and size)."""
    W, H = 2 * cols, 4 * rows
    outline = np.zeros((H, W), dtype=bool)
    for ring in rings:
        x, y = view.to_dots(ring[:, 0], ring[:, 1], cols, rows)
        for i in range(len(x) - 1):
            lx, ly = _line_dots(x[i], y[i], x[i + 1], y[i + 1])
            ok = (lx >= 0) & (lx < W) & (ly >= 0) & (ly < H)
            outline[ly[ok], lx[ok]] = True
    return outline


def render(view: View, cols: int, rows: int, L: Layers) -> list[list[tuple[str, str]]]:
    """Rows of (text, rich style) runs."""
    W, H = 2 * cols, 4 * rows
    outline = (
        L.outline_mask if L.outline_mask is not None else outline_mask(view, cols, rows, L.outline)
    )
    plane = np.zeros((H, W), dtype=bool)
    hot_char = np.zeros((rows, cols), dtype=bool)
    if L.aircraft is not None and len(L.aircraft):
        x, y = view.to_dots(L.aircraft[:, 0], L.aircraft[:, 1], cols, rows)
        ok = (x >= 0) & (x < W) & (y >= 0) & (y < H)
        plane[y[ok], x[ok]] = True
        if L.detecting is not None:
            d = ok & np.asarray(L.detecting, bool)
            hot_char[y[d] // 4, x[d] // 2] = True
            # make detections bolder: fill the whole 2x4 cell
            for cy, cx in zip(y[d] // 4, x[d] // 2, strict=True):
                plane[cy * 4 : cy * 4 + 4, cx * 2 : cx * 2 + 2] = True

    bg = np.zeros((rows, cols, 3), dtype=np.uint8)
    if L.wind_speed is not None:
        ws = np.nan_to_num(L.wind_speed, nan=0.0)
        bg = _ramp(WIND_STOPS, ws)
        bg[np.isnan(L.wind_speed)] = 0
    turb = np.zeros((rows, cols), dtype=bool)
    if L.turb_p is not None:
        lit = np.isfinite(L.turb_p) & (L.turb_p > L.floor)
        t = np.clip(
            np.log(np.where(lit, L.turb_p, L.floor) / L.floor) / np.log(0.1 / L.floor), 0, 1
        )
        bg[lit] = _ramp(TURB_STOPS, t[lit])
        turb = lit

    glyph: dict[tuple[int, int], tuple[str, str]] = {}
    for lon, lat, sev in L.pireps:
        x, y = view.to_dots(np.array([lon]), np.array([lat]), cols, rows)
        cx, cy = int(x[0]) // 2, int(y[0]) // 4
        if 0 <= cx < cols and 0 <= cy < rows:
            glyph[(cy, cx)] = ("^", "bold #ff5040" if sev else "bold #ffb02e")

    out: list[list[tuple[str, str]]] = []
    for r in range(rows):
        runs: list[tuple[str, str]] = []
        for c in range(cols):
            b = bg[r, c]
            bgs = f"on #{b[0]:02x}{b[1]:02x}{b[2]:02x}" if b.any() else ""
            if (r, c) in glyph:
                ch, fg = glyph[(r, c)]
            else:
                code = 0
                block_p = plane[r * 4 : r * 4 + 4, c * 2 : c * 2 + 2]
                block_o = outline[r * 4 : r * 4 + 4, c * 2 : c * 2 + 2]
                for dy in range(4):
                    for dx in range(2):
                        if block_p[dy, dx] or block_o[dy, dx]:
                            code |= BRAILLE_BITS[dy][dx]
                ch = chr(0x2800 + code) if code else " "
                if hot_char[r, c]:
                    fg = "bold #ffb02e"
                elif block_p.any():
                    fg = "#f2f5fa" if not turb[r, c] else "bold #ffffff"
                else:
                    fg = "#5d6b80"
            style = f"{fg} {bgs}".strip()
            if runs and runs[-1][1] == style:
                runs[-1] = (runs[-1][0] + ch, style)
            else:
                runs.append((ch, style))
        out.append(runs)
    return out
