"""Daily HTS sync.

In-process: FastAPI lifespan calls `daily_crawl_loop()` (see backend/api/main.py).
Manual / cron:
    uv run python -m backend.tariffs.scheduler --once
    uv run python -m backend.tariffs.scheduler --once --chapters 85 99   # quick demo load

Env:
    TARIFF_CRAWL          daily | off            (default daily)
    TARIFF_CRAWL_TIME     HH:MM                  (default 06:00)
    TARIFF_CRAWL_TZ       IANA tz                (default America/New_York — USITC posts on US business days)
    TARIFF_CRAWL_ON_START true | false           (default false; also crawls at startup if DB is empty)
    TARIFF_DB_PATH        SQLite path            (default runtime/tariffs.db)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.tariffs.crawler import run_crawl
from backend.tariffs.store import TariffStore

log = logging.getLogger("tariffs")


def seconds_until_next_run(now: datetime | None = None) -> float:
    tz = ZoneInfo(os.getenv("TARIFF_CRAWL_TZ", "America/New_York"))
    hh, mm = (int(x) for x in os.getenv("TARIFF_CRAWL_TIME", "06:00").split(":"))
    now = (now or datetime.now(tz)).astimezone(tz)
    nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


async def _crawl_safely() -> None:
    try:
        result = await asyncio.to_thread(run_crawl)
        log.info("HTS sync finished: %s", {k: result[k] for k in ("status", "lines_seen", "added", "removed", "modified")})
    except Exception:  # never take the API server down because USITC is unreachable
        log.exception("HTS sync failed")


async def daily_crawl_loop() -> None:
    if os.getenv("TARIFF_CRAWL", "daily").lower() == "off":
        log.info("HTS daily sync disabled (TARIFF_CRAWL=off)")
        return
    store = TariffStore()
    empty = store.last_run() is None
    store.close()
    if os.getenv("TARIFF_CRAWL_ON_START", "false").lower() == "true":
        log.info("HTS sync on startup (empty DB=%s)", empty)
        await _crawl_safely()
    while True:
        wait = seconds_until_next_run()
        log.info("Next HTS sync in %.1f h", wait / 3600)
        await asyncio.sleep(wait)
        await _crawl_safely()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Sync the US HTS tariff schedule")
    p.add_argument("--once", action="store_true", help="run one sync and exit")
    p.add_argument("--chapters", type=int, nargs="*", help="limit to these chapters")
    args = p.parse_args()
    if args.once:
        print(json.dumps(run_crawl(chapters=args.chapters), indent=2))
    else:
        asyncio.run(daily_crawl_loop())


if __name__ == "__main__":
    main()
