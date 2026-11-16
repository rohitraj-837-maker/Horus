"""Point -> US state code, for plain-language event lines ("over CO").

Uses the bundled Natural Earth 1:110m state outlines (horus/data/conus_states.json).
"""

from __future__ import annotations

import json

import numpy as np
from matplotlib.path import Path as MplPath

from horus import config


class Places:
    def __init__(self, path: str = "horus/data/conus_states.json") -> None:
        data = json.loads(config.resolve(path).read_text())
        self.rings: list[tuple[str, tuple[float, float, float, float], MplPath]] = []
        for st in data["states"]:
            for r in st["rings"]:
                a = np.asarray(r, dtype=float)
                box = (a[:, 0].min(), a[:, 0].max(), a[:, 1].min(), a[:, 1].max())
                self.rings.append((st["code"], box, MplPath(a)))

    def state(self, lon: float, lat: float) -> str | None:
        for code, (x0, x1, y0, y1), p in self.rings:
            if x0 <= lon <= x1 and y0 <= lat <= y1 and p.contains_point((lon, lat)):
                return code
        return None

    def where(self, lon: float, lat: float) -> str:
        s = self.state(lon, lat)
        return f"over {s}" if s else "offshore"
