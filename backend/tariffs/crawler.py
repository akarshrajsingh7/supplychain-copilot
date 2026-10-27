"""Daily HTS crawl: fetch every chapter from the USITC API, diff against the
local snapshot, and record what changed."""
from __future__ import annotations

import logging
import time
from typing import Any

from backend.tariffs.hts_client import CHAPTERS, HTSClient
from backend.tariffs.store import TariffStore, default_db_path, normalize

log = logging.getLogger(__name__)

try:  # LangSmith tracing is optional; it activates when LANGSMITH_TRACING=true
    from langsmith import traceable
except ImportError:  # pragma: no cover
    def traceable(*_a, **_k):
        return lambda f: f


@traceable(name="hts_daily_crawl", run_type="chain")
def run_crawl(
    db_path: str | None = None,
    chapters: list[int] | None = None,
    client: HTSClient | None = None,
) -> dict[str, Any]:
    store = TariffStore(db_path or default_db_path())
    client = client or HTSClient()
    run_id = store.start_run()
    totals = {"lines_seen": 0, "added": 0, "removed": 0, "modified": 0, "failed_chapters": []}
    try:
        for ch in chapters or CHAPTERS:
            try:
                raw = client.export_chapter(ch)
            except Exception as exc:  # one bad chapter shouldn't kill the run
                log.error("Chapter %02d failed: %s", ch, exc)
                totals["failed_chapters"].append(ch)
                continue
            rows = [r for r in (normalize(x, ch) for x in raw) if r]
            if not rows:
                # Empty response for a chapter that previously had data is suspicious —
                # skip instead of marking every line "removed".
                if store.get_prefix(f"{ch:02d}", limit=1):
                    log.warning("Chapter %02d returned no rows; keeping previous snapshot", ch)
                    totals["failed_chapters"].append(ch)
                continue
            stats = store.sync_chapter(run_id, ch, rows)
            for k in ("lines_seen", "added", "removed", "modified"):
                totals[k] += stats[k]
            log.info("Chapter %02d: %s", ch, stats)
            time.sleep(getattr(client, "delay", 0))  # be polite to a public gov service

        status = "partial" if totals["failed_chapters"] else "success"
        if totals["lines_seen"] == 0:
            status = "failed"
        store.finish_run(run_id, status, totals)
        return {"run_id": run_id, "status": status, **totals}
    except Exception as exc:
        store.finish_run(run_id, "failed", totals, error=str(exc))
        raise
    finally:
        store.close()
