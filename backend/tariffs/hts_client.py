"""Client for the official USITC HTS REST API (https://hts.usitc.gov/reststop).

Endpoints (per the USITC HTS External User Guide):
  GET /reststop/search?keyword=...                       -> up to 100 matching articles (JSON)
  GET /reststop/exportList?from=..&to=..&format=JSON&styles=false -> all articles in a range

We use the official API instead of scraping HTML: it is stable, structured,
and is what USITC publishes for programmatic access.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterator

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://hts.usitc.gov/reststop"
# Chapter 77 is reserved (unused) in the HTS; 98/99 hold special provisions and
# the temporary / additional duties (e.g. Section 232, 301), which change most often.
CHAPTERS = [c for c in range(1, 100) if c != 77]


class HTSClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: float = 60.0,
        max_retries: int = 4,
        delay_between_requests: float = 1.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.delay = delay_between_requests
        self.session = session or requests.Session()
        self.session.headers.setdefault(
            "User-Agent", "SupplyChainCopilot-TariffCrawler/1.0 (+daily HTS sync)"
        )

    # -- low level ---------------------------------------------------------
    def _get(self, path: str, params: dict[str, str]) -> Any:
        url = f"{self.base_url}/{path}"
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                # The guide documents 500 as the API's error code; treat 5xx/429 as retryable.
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                resp.raise_for_status()
                if not resp.content.strip():
                    return []
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                if attempt == self.max_retries:
                    raise
                backoff = 2 ** attempt
                log.warning("HTS %s %s failed (%s); retry %d in %ss", path, params, exc, attempt, backoff)
                time.sleep(backoff)
        return []  # unreachable

    # -- public API --------------------------------------------------------
    def export_range(self, start: str, end: str) -> list[dict[str, Any]]:
        data = self._get(
            "exportList",
            {"from": start, "to": end, "format": "JSON", "styles": "false"},
        )
        return data if isinstance(data, list) else []

    def export_chapter(self, chapter: int) -> list[dict[str, Any]]:
        ch = f"{chapter:02d}"
        return self.export_range(f"{ch}01", f"{ch}99.99.99.99")

    def search(self, keyword: str) -> list[dict[str, Any]]:
        data = self._get("search", {"keyword": keyword})
        return data if isinstance(data, list) else []

    def iter_all_chapters(self, chapters: list[int] | None = None) -> Iterator[tuple[int, list[dict]]]:
        for ch in chapters or CHAPTERS:
            yield ch, self.export_chapter(ch)
            time.sleep(self.delay)  # be polite to a public government service
