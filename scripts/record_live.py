"""Record live ADS-B snapshots over CONUS to hourly Parquet (Plan B archive).

    uv run python scripts/record_live.py --duration 300      # 5-minute smoke run
    nohup uv run python scripts/record_live.py &             # run until stopped

Stop with Ctrl-C or SIGTERM; buffered rows are flushed on exit.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from turbcast import config
from turbcast.ingest import live


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--duration", type=float, default=None, help="seconds (default: forever)")
    ap.add_argument("--source", choices=["opensky", "adsblol"], default=None)
    ap.add_argument("--config", default=str(config.DEFAULT_PATH))
    args = ap.parse_args()

    cfg = config.load(args.config)
    source_name = args.source or cfg.live.source
    out = config.resolve(cfg.live.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(out / f"recorder-{source_name}.log"),
        ],
    )
    log = logging.getLogger("record_live")

    source: live.Source
    if source_name == "opensky":
        creds = live.load_credentials(config.resolve(cfg.live.credentials_path))
        log.info("OpenSky auth: %s", "API client" if creds else "anonymous (400 credits/day)")
        source = live.OpenSkySource(cfg, live.TokenManager(creds))
    else:
        source = live.AdsbLolSource(cfg)
        log.info("adsb.lol: %d tiles per sweep", len(source.tiles))

    async def _run() -> live.Recorder:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        return await live.run(cfg, source, duration_s=args.duration, stop=stop)

    asyncio.run(_run())
    log.info("recorder stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
