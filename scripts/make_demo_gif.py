"""Render docs/watch-sample.txt (real `make watch` output) as a terminal-style GIF.

    uv run python scripts/make_demo_gif.py      # -> docs/img/horus-watch.gif

Lines appear one at a time like the live stream, then the summary holds. Font:
DejaVu Sans Mono shipped with matplotlib, so the output is the same on any machine.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import matplotlib
from PIL import Image, ImageDraw, ImageFont

from horus import config

BG = (10, 12, 15)
INK = (226, 228, 232)
DIM = (110, 116, 128)
AMBER = (255, 176, 46)
GOLD = (215, 175, 95)
ORANGE = (255, 135, 95)
RED = (255, 80, 64)
W, ROWS, SIZE, PAD = 1180, 26, 14, 18
INDENT = 23  # column where event text starts: "HH:MMZ  LABEL         "


def fonts() -> tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    d = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    return (
        ImageFont.truetype(str(d / "DejaVuSansMono.ttf"), SIZE),
        ImageFont.truetype(str(d / "DejaVuSansMono-Bold.ttf"), SIZE),
    )


def style(line: str) -> tuple[tuple[int, int, int], bool]:
    label = line[9:22].strip() if re.match(r"^\s*\d\d:\d\dZ  ", line) else ""
    if label == "TURBULENCE":
        return AMBER, True
    if label == "FORECAST":
        return GOLD, False
    if label == "PILOT REPORT":
        return (RED if " severe" in line else ORANGE), False
    if label in ("calm", "·"):
        return DIM, False
    if line.startswith(("HORUS", "Summary")):
        return INK, True
    if line.startswith(("  TURBULENCE", "  calm", "  FORECAST", "  PILOT", "Research", "Lines")):
        return DIM, False
    return INK, False


def wrap(line: str, cols: int) -> list[str]:
    if len(line) <= cols:
        return [line]
    cut = line.find("  Horus:")
    if 0 < cut <= cols:
        rest = " " * INDENT + line[cut + 2 :]
        return [line[:cut], *wrap(rest, cols)]
    sp = line.rfind(" ", 0, cols)
    if sp <= 0:
        return [line[: cols - 1] + "…"]
    lead = len(line) - len(line.lstrip())
    return [line[:sp], *wrap(" " * (lead + 2) + line[sp + 1 :], cols)]


def main() -> int:
    src = config.resolve("docs/watch-sample.txt").read_text().splitlines()
    src = [ln for ln in src if not ln.startswith("Loading")]
    reg, bold = fonts()
    cw = reg.getlength("M")
    lh = int(SIZE * 1.55)
    cols = int((W - 2 * PAD) / cw)
    H = PAD * 2 + lh * ROWS
    shown: list[tuple[str, tuple[int, int, int], bool]] = []
    frames: list[Image.Image] = []
    durations: list[int] = []

    def frame(ms: int) -> None:
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im)
        for i, (text, col, b) in enumerate(shown[-ROWS:]):
            d.text((PAD, PAD + i * lh), text, font=bold if b else reg, fill=col)
        frames.append(im)
        durations.append(ms)

    body_start = next(i for i, ln in enumerate(src) if re.match(r"^\s*\d\d:\d\dZ  ", ln))
    for ln in src[:body_start]:
        col, b = style(ln)
        shown.extend((part, col, b) for part in wrap(ln, cols))
    frame(2600)
    for ln in src[body_start:]:
        col, b = style(ln)
        shown.extend((part, col, b) for part in wrap(ln, cols))
        if ln.strip():
            label = ln[9:22].strip()
            frame(220 if label in ("·", "calm") else 520)
    frame(5000)
    out = config.resolve("docs/img/horus-watch.gif")
    out.parent.mkdir(parents=True, exist_ok=True)
    # one shared palette (the last frame holds every colour used) keeps frames small
    pal = frames[-1].quantize(colors=32, method=Image.Quantize.MEDIANCUT)
    qs = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    qs[0].save(out, save_all=True, append_images=qs[1:], duration=durations, loop=0, optimize=True)
    print(f"{out}: {len(frames)} frames, {out.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
