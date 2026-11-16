"""Screenshot the running map through Chrome's DevTools protocol (README / docs images).

    uv run python scripts/ui_shot.py URL OUT.png WAIT_S [JS]

Plain `chrome --screenshot` fires at page load, before the WebSocket feed and the
render loop have produced anything; this waits WAIT_S seconds, optionally runs JS
in the page (then waits 3 s more), and captures. Software WebGL (SwiftShader) is
enabled so WebGL layers render headless.
"""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9333


def main() -> int:
    url, out, wait = sys.argv[1], sys.argv[2], float(sys.argv[3])
    js = sys.argv[4] if len(sys.argv) > 4 else ""
    prof = tempfile.mkdtemp()
    p = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={prof}",
            "--window-size=1400,850",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        tabs = None
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                break
            except OSError:
                time.sleep(0.2)
        if tabs is None:
            print("chrome did not start")
            return 1
        ws_url = next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"]

        async def run() -> None:
            async with websockets.connect(ws_url, max_size=None) as ws:
                n = 0

                async def call(method: str, **params: object) -> dict:
                    nonlocal n
                    n += 1
                    await ws.send(json.dumps({"id": n, "method": method, "params": params}))
                    while True:
                        r = json.loads(await ws.recv())
                        if r.get("id") == n:
                            return r

                await call(
                    "Emulation.setDeviceMetricsOverride",
                    width=1400,
                    height=850,
                    deviceScaleFactor=1,
                    mobile=False,
                )
                await call("Page.navigate", url=url)
                await asyncio.sleep(wait)
                if js:
                    r = await call("Runtime.evaluate", expression=js)
                    if r.get("result", {}).get("exceptionDetails"):
                        print("js error:", r["result"]["exceptionDetails"].get("text"))
                    await asyncio.sleep(3)
                shot = await call("Page.captureScreenshot", format="png")
                with open(out, "wb") as f:
                    f.write(base64.b64decode(shot["result"]["data"]))

        asyncio.run(run())
        print("saved", out)
        return 0
    finally:
        p.terminate()
        p.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
