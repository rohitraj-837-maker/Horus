"""Build the offline CONUS state outline used by the demo map.

Source: Natural Earth 1:110m admin-1 states/provinces (public domain),
https://github.com/nvkelso/natural-earth-vector (geojson/ne_110m_admin_1_states_provinces.geojson).
Keeps the lower 48 + DC, drops attributes, rounds coordinates to 0.01 deg.

    uv run python -m demo.build_basemap path/to/ne_110m_admin_1_states_provinces.geojson
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

OUT = Path(__file__).parent / "data" / "conus_states.json"
DROP = {"AK", "HI"}


def _ring(r: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for lon, lat, *_ in r:
        p = [round(lon, 2), round(lat, 2)]
        if not out or out[-1] != p:
            out.append(p)
    return out


def convert(src: dict[str, Any]) -> dict[str, Any]:
    states = []
    for f in src["features"]:
        code = f["properties"].get("postal")
        if not code or code in DROP:
            continue
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        states.append({"code": code, "rings": [_ring(poly[0]) for poly in polys]})
    states.sort(key=lambda s: s["code"])
    return {"source": "Natural Earth 1:110m admin-1 (public domain)", "states": states}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    data = convert(json.loads(Path(sys.argv[1]).read_text()))
    OUT.write_text(json.dumps(data, separators=(",", ":")))
    print(f"{OUT}: {len(data['states'])} states, {OUT.stat().st_size / 1e3:.0f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
