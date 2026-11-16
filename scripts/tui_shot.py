"""Save SVG screenshots of the terminal dashboard on real data (docs/img/).

    uv run python scripts/tui_shot.py

Runs the TUI headless with the real hub (archive day + trained models + winds),
lets the live feed run, then captures live mode and the second case study."""

from __future__ import annotations

import asyncio

from horus import config
from horus.tui.app import HorusTUI


async def main() -> None:
    app = HorusTUI()
    async with app.run_test(size=(150, 46)) as pilot:
        for _ in range(240):  # the hub loads a day of tracks (~20-40 s)
            await pilot.pause(0.5)
            if app.started:
                break
        await pilot.pause(45)  # let the live loop warm up the grid
        app.save_screenshot(str(config.resolve("docs/img/horus-tui-live.svg")))
        await pilot.press("c")
        await pilot.press("c")
        for _ in range(22):
            await pilot.pause(0.5)
        app.action_pause()
        await pilot.pause(0.6)
        app.save_screenshot(str(config.resolve("docs/img/horus-tui-case.svg")))


if __name__ == "__main__":
    asyncio.run(main())
