"""LangChain tools that let the SupplyChain Copilot agent use the crawled tariff data.

Usage:
    from tariff_crawler.tools import TARIFF_TOOLS
    create_deep_agent(..., tools=[..., *TARIFF_TOOLS])  # see backend/agents/commander.py
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from langchain_core.tools import tool

from backend.tariffs.store import TariffStore, default_db_path

_store: TariffStore | None = None


def _db() -> TariffStore:
    global _store
    if _store is None:
        _store = TariffStore(default_db_path())
    return _store


def _fmt(line: dict) -> dict:
    return {
        "hts": line["htsno"],
        "description": line["description"],
        "units": json.loads(line["units"] or "[]"),
        "general_rate": line["general"] or None,
        "special_rate": line["special"] or None,
        "column2_rate": line["other"] or None,
        "footnotes": json.loads(line["footnotes"] or "[]"),
        "last_verified": line["last_seen"],
    }


def _freshness() -> str:
    run = _db().last_run()
    if not run:
        return "No tariff crawl has run yet."
    return f"Tariff data last synced {run['finished_at'] or run['started_at']} (status: {run['status']})."


@tool
def lookup_hts_code(hts_code: str) -> str:
    """Look up US import duty rates for an HTS code (4, 6, 8 or 10 digits, e.g. '8541.43.00' or '854143').
    Returns the line, its parent lines (rates are often set at the 8-digit level), general/special/
    column-2 rates, and footnotes (which point to Chapter 99 additional duties such as Section 301/232)."""
    digits = "".join(c for c in hts_code if c.isdigit())
    if len(digits) < 4:
        return "Provide at least a 4-digit HTS heading."
    parts = [digits[:4]] + [digits[i:i + 2] for i in range(4, min(len(digits), 10), 2)]
    dotted = ".".join(parts)
    db = _db()
    line = db.get_line(dotted)
    if line:
        result = {"match": _fmt(line), "parents": [_fmt(p) for p in db.ancestors(dotted)]}
    else:
        children = db.get_prefix(dotted, limit=25)
        if not children:
            return f"No HTS line found for {dotted}. {_freshness()}"
        result = {"note": f"No exact line {dotted}; showing sub-lines.", "lines": [_fmt(c) for c in children]}
    result["data_freshness"] = _freshness()
    return json.dumps(result, ensure_ascii=False)


@tool
def search_tariffs(product_description: str) -> str:
    """Search the US Harmonized Tariff Schedule by product description (e.g. 'lithium-ion batteries',
    'copper cathodes') to find candidate HTS codes and their duty rates. Classification is ultimately
    the importer's responsibility — treat results as candidates to verify."""
    hits = _db().search(product_description, limit=15)
    if not hits:
        return f"No matching tariff lines. {_freshness()}"
    return json.dumps({"results": [_fmt(h) for h in hits], "data_freshness": _freshness()}, ensure_ascii=False)


@tool
def recent_tariff_changes(days: int = 7, hts_prefix: str = "") -> str:
    """List US tariff schedule changes detected by the daily crawler in the last N days.
    Optionally filter by HTS prefix (e.g. '85' for chapter 85, '9903' for Chapter 99 additional duties).
    Use this to warn about rate changes affecting a supply chain."""
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
    prefix = "".join(c for c in hts_prefix if c.isdigit() or c == ".")
    changes = _db().recent_changes(since, prefix, limit=100)
    if not changes:
        return f"No tariff changes detected in the last {days} days. {_freshness()}"
    slim = [{k: c[k] for k in ("detected_at", "htsno", "change_type", "field", "old_value", "new_value")}
            for c in changes]
    return json.dumps({"changes": slim, "count": len(slim), "data_freshness": _freshness()}, ensure_ascii=False)


TARIFF_TOOLS = [lookup_hts_code, search_tariffs, recent_tariff_changes]
