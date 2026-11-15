"""Offline Horus demo server (standard library only, binds 127.0.0.1).

    uv run python -m demo.server [--port 8765]

Routes
  /                      single-page UI (demo/static/index.html)
  /static/<file>         UI assets (whitelisted names only)
  /api/health            {"ok": true, "offline": true, ...}
  /api/scenarios         scenario index
  /api/scenario/<id>     one bundled synthetic scenario
  /api/basemap           CONUS state outline (Natural Earth, public domain)
  /api/about             pipeline stages, status and attributions
  /api/overview          landing page: computed synthetic KPIs, pipeline, maths, benchmark plan

A Content-Security-Policy restricts the page to this origin, so the browser itself
blocks any attempt to load tiles, fonts or scripts from the internet.
"""

from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from demo.about import ABOUT
from demo.overview import overview

HERE = Path(__file__).parent
STATIC = HERE / "static"
DATA = HERE / "data"
TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",  # Geist + Instrument Serif (SIL OFL, demo/fonts-licenses/)
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'"
)


def scenario_ids() -> list[str]:
    return [s["id"] for s in json.loads((DATA / "scenarios.json").read_text())]


class Handler(BaseHTTPRequestHandler):
    server_version = "HorusDemo/1"

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the terminal quiet
        if self.server.verbose:  # type: ignore[attr-defined]
            super().log_message(fmt, *args)

    def _send(self, body: bytes, ctype: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        binary = ctype.startswith(("font/", "image/png"))
        self.send_header("Content-Type", ctype if binary else f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any) -> None:
        self._send(json.dumps(obj).encode(), "application/json")

    def _file(self, path: Path, ctype: str) -> None:
        self._send(path.read_bytes(), ctype)

    def _404(self) -> None:
        self._send(b'{"error": "not found"}', "application/json", HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:  # noqa: N802 (stdlib name)
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._file(STATIC / "index.html", "text/html")
        if path.startswith("/static/"):
            name = path.removeprefix("/static/")
            allowed = {p.name: p for p in STATIC.iterdir() if p.suffix in TYPES}
            if name in allowed:
                return self._file(allowed[name], TYPES[allowed[name].suffix])
            return self._404()
        if path == "/api/health":
            return self._json({"ok": True, "offline": True, "scenarios": len(scenario_ids())})
        if path == "/api/scenarios":
            return self._file(DATA / "scenarios.json", "application/json")
        if path.startswith("/api/scenario/"):
            sid = path.removeprefix("/api/scenario/")
            if sid in scenario_ids():
                return self._file(DATA / f"scenario-{sid}.json", "application/json")
            return self._404()
        if path == "/api/basemap":
            return self._file(DATA / "conus_states.json", "application/json")
        if path == "/api/about":
            return self._json(ABOUT)
        if path == "/api/overview":
            return self._json(overview())
        return self._404()


def make_server(port: int = 8765, verbose: bool = False) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.verbose = verbose  # type: ignore[attr-defined]
    return srv


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline Horus demo")
    ap.add_argument("--port", type=int, default=8765, help="first port to try")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    srv = None
    for port in range(args.port, args.port + 20):
        try:
            srv = make_server(port, args.verbose)
            break
        except OSError:  # port in use (e.g. another project's demo): try the next one
            print(f"port {port} is busy, trying {port + 1}", file=sys.stderr)
    if srv is None:
        print(f"no free port in {args.port}-{args.port + 19}", file=sys.stderr)
        return 1
    print(f"Horus demo (offline, synthetic data): http://127.0.0.1:{srv.server_port}/", flush=True)
    print("Ctrl-C to stop.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
