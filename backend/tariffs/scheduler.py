"""Daily HTS sync + status for the dashboard.

In-process: FastAPI lifespan calls `daily_crawl_loop()` (see backend/api/main.py), and the
dashboard reads `sync_status()` / triggers `trigger_sync()` through /api/tariffs/*.
Manual / cron:
    uv run python -m backend.tariffs.scheduler --once
    uv run python -m backend.tariffs.scheduler --once --chapters 85 99   # quick demo load

Env:
    TARIFF_CRAWL          daily | off            (default daily)
    TARIFF_CRAWL_TIME     HH:MM                  (default 06:00)
    TARIFF_CRAWL_TZ       IANA tz                (default America/New_York — USITC posts on US business days)
    TARIFF_CRAWL_ON_START true | false           (default false)
    TARIFF_DB_PATH        SQLite path            (default runtime/tariffs.db)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
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


def _enabled() -> bool:
    return os.getenv("TARIFF_CRAWL", "daily").lower() != "off"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class SyncController:
    """One sync at a time, whether started by the daily loop or the dashboard."""

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.next_run_at: str | None = None
        self.progress: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def _on_progress(self, chapter: int, done: int, total: int) -> None:  # called from worker thread
        self.progress.update(chapter=chapter, done=done, total=total)

    async def _run(self, chapters: list[int] | None, trigger: str) -> None:
        self.progress = {"chapter": None, "done": 0, "total": len(chapters) if chapters else 98,
                         "trigger": trigger, "chapters": chapters, "started_at": _now()}
        try:
            result = await asyncio.to_thread(run_crawl, chapters=chapters, on_progress=self._on_progress)
            log.info("HTS sync (%s) finished: %s", trigger,
                     {k: result[k] for k in ("status", "lines_seen", "added", "removed", "modified")})
        except Exception:  # never take the API server down because USITC is unreachable
            log.exception("HTS sync (%s) failed", trigger)

    def trigger(self, chapters: list[int] | None = None, trigger: str = "manual") -> bool:
        """Start a sync in the background. Returns False if one is already running."""
        if self.running:
            return False
        self.task = asyncio.create_task(self._run(chapters, trigger))
        return True

    async def wait(self) -> None:
        if self.task:
            await self.task

    def status(self) -> dict[str, Any]:
        store = TariffStore()
        try:
            last = store.last_run()
            if last:
                last["failed_chapters"] = json.loads(last.get("failed_chapters") or "[]")
            since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            changes_7d = store.conn.execute(
                "SELECT COUNT(*) FROM tariff_changes WHERE detected_at >= ?", (since,)).fetchone()[0]
            lines = store.conn.execute("SELECT COUNT(*), COUNT(DISTINCT chapter) FROM tariff_lines").fetchone()
        finally:
            store.close()
        return {
            "source": "USITC Harmonized Tariff Schedule · hts.usitc.gov/reststop",
            "enabled": _enabled(),
            "schedule": f"Daily {os.getenv('TARIFF_CRAWL_TIME', '06:00')} {os.getenv('TARIFF_CRAWL_TZ', 'America/New_York')}",
            "next_run_at": self.next_run_at if _enabled() else None,
            "running": self.running,
            "progress": self.progress if self.running else None,
            "last_run": last,
            "total_lines": lines[0],
            "chapters_loaded": lines[1],
            "changes_7d": changes_7d,
        }


controller = SyncController()


def recent_changes(days: int = 30, limit: int = 25) -> list[dict]:
    store = TariffStore()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
        rows = store.recent_changes(since, "", limit)
        for r in rows:
            line = store.get_line(r["htsno"])
            r["description"] = line["description"] if line else None
        return rows
    finally:
        store.close()


async def daily_crawl_loop() -> None:
    if not _enabled():
        log.info("HTS daily sync disabled (TARIFF_CRAWL=off)")
        return
    if os.getenv("TARIFF_CRAWL_ON_START", "false").lower() == "true":
        controller.trigger(trigger="startup")
        await controller.wait()
    while True:
        wait = seconds_until_next_run()
        controller.next_run_at = (datetime.now(timezone.utc) + timedelta(seconds=wait)).replace(microsecond=0).isoformat()
        log.info("Next HTS sync in %.1f h", wait / 3600)
        await asyncio.sleep(wait)
        if controller.trigger(trigger="daily"):
            await controller.wait()


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
