"""SQLite storage for tariff lines, change history, and crawl runs."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS tariff_lines (
    htsno        TEXT PRIMARY KEY,
    chapter      TEXT NOT NULL,
    indent       INTEGER,
    description  TEXT,
    units        TEXT,          -- JSON list
    general      TEXT,          -- Column 1 General / MFN rate
    special      TEXT,          -- Column 1 Special (FTA / preference programs)
    other        TEXT,          -- Column 2 rate
    footnotes    TEXT,          -- JSON list of footnote texts (often refs to ch. 99)
    content_hash TEXT NOT NULL,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lines_chapter ON tariff_lines(chapter);

CREATE TABLE IF NOT EXISTS tariff_changes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL,
    detected_at  TEXT NOT NULL,
    htsno        TEXT NOT NULL,
    change_type  TEXT NOT NULL,  -- added | removed | modified
    field        TEXT,           -- which field changed (for modified)
    old_value    TEXT,
    new_value    TEXT
);
CREATE INDEX IF NOT EXISTS idx_changes_time ON tariff_changes(detected_at);
CREATE INDEX IF NOT EXISTS idx_changes_hts ON tariff_changes(htsno);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,  -- running | success | partial | failed
    lines_seen    INTEGER DEFAULT 0,
    added         INTEGER DEFAULT 0,
    removed       INTEGER DEFAULT 0,
    modified      INTEGER DEFAULT 0,
    failed_chapters TEXT,
    error         TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS tariff_fts USING fts5(
    htsno UNINDEXED, description, tokenize='porter'
);
"""

TRACKED_FIELDS = ("description", "units", "general", "special", "other", "footnotes", "indent")


def default_db_path() -> Path:
    """runtime/ is git-ignored; override with TARIFF_DB_PATH."""
    env = os.getenv("TARIFF_DB_PATH")
    if env:
        return Path(env)
    from backend.config import RUNTIME
    return RUNTIME / "tariffs.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(raw: dict[str, Any], chapter: int) -> dict[str, Any] | None:
    """Turn a raw API record into a flat row. Returns None for heading-only rows w/o number."""
    htsno = (raw.get("htsno") or "").strip()
    desc = (raw.get("description") or "").strip()
    if not htsno:
        return None  # un-numbered group headings ("Other:", etc.) — not addressable
    footnotes = []
    for fn in raw.get("footnotes") or []:
        if isinstance(fn, dict):
            text = (fn.get("value") or "").strip()
            cols = fn.get("columns") or []
            if text:
                footnotes.append(f"[{','.join(cols)}] {text}" if cols else text)
        elif fn:
            footnotes.append(str(fn).strip())
    row = {
        "htsno": htsno,
        "chapter": f"{chapter:02d}",
        "indent": int(raw.get("indent") or 0),
        "description": desc,
        "units": json.dumps(raw.get("units") or []),
        "general": (raw.get("general") or "").strip(),
        "special": (raw.get("special") or "").strip(),
        "other": (raw.get("other") or "").strip(),
        "footnotes": json.dumps(footnotes),
    }
    row["content_hash"] = hashlib.sha256(
        json.dumps({k: row[k] for k in TRACKED_FIELDS}, sort_keys=True).encode()
    ).hexdigest()
    return row


class TariffStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # -- runs --------------------------------------------------------------
    def start_run(self) -> int:
        cur = self.conn.execute(
            "INSERT INTO crawl_runs(started_at, status) VALUES (?, 'running')", (now_iso(),)
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, status: str, stats: dict[str, Any], error: str | None = None) -> None:
        self.conn.execute(
            """UPDATE crawl_runs SET finished_at=?, status=?, lines_seen=?, added=?, removed=?,
               modified=?, failed_chapters=?, error=? WHERE id=?""",
            (now_iso(), status, stats.get("lines_seen", 0), stats.get("added", 0),
             stats.get("removed", 0), stats.get("modified", 0),
             json.dumps(stats.get("failed_chapters", [])), error, run_id),
        )
        self.conn.commit()

    def last_run(self) -> dict | None:
        r = self.conn.execute("SELECT * FROM crawl_runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    # -- sync one chapter ----------------------------------------------------
    def sync_chapter(self, run_id: int, chapter: int, rows: Iterable[dict[str, Any]]) -> dict[str, int]:
        """Upsert a chapter's rows and log diffs. Lines missing from this chapter = removed."""
        ch = f"{chapter:02d}"
        ts = now_iso()
        existing = {
            r["htsno"]: dict(r)
            for r in self.conn.execute("SELECT * FROM tariff_lines WHERE chapter=?", (ch,))
        }
        # A chapter with nothing stored yet (first run, or it failed on earlier runs)
        # is a baseline load — don't log thousands of "added" changes for it.
        is_initial_load = not existing
        seen: set[str] = set()
        stats = {"lines_seen": 0, "added": 0, "removed": 0, "modified": 0}
        changes: list[tuple] = []

        for row in rows:
            if row["htsno"] in seen:
                continue
            seen.add(row["htsno"])
            stats["lines_seen"] += 1
            old = existing.get(row["htsno"])
            if old is None:
                self.conn.execute(
                    """INSERT INTO tariff_lines(htsno, chapter, indent, description, units, general,
                       special, other, footnotes, content_hash, first_seen, last_seen)
                       VALUES (:htsno,:chapter,:indent,:description,:units,:general,:special,:other,
                       :footnotes,:content_hash,:ts,:ts)""",
                    {**row, "ts": ts},
                )
                self.conn.execute("INSERT INTO tariff_fts(htsno, description) VALUES (?,?)",
                                  (row["htsno"], row["description"]))
                if not is_initial_load:  # don't log 30k "added" rows on first ever run
                    stats["added"] += 1
                    changes.append((run_id, ts, row["htsno"], "added", None, None, row["general"]))
            elif old["content_hash"] != row["content_hash"]:
                stats["modified"] += 1
                for f in TRACKED_FIELDS:
                    if str(old[f]) != str(row[f]):
                        changes.append((run_id, ts, row["htsno"], "modified", f, str(old[f]), str(row[f])))
                self.conn.execute(
                    """UPDATE tariff_lines SET indent=:indent, description=:description, units=:units,
                       general=:general, special=:special, other=:other, footnotes=:footnotes,
                       content_hash=:content_hash, last_seen=:ts WHERE htsno=:htsno""",
                    {**row, "ts": ts},
                )
                if old["description"] != row["description"]:
                    self.conn.execute("DELETE FROM tariff_fts WHERE htsno=?", (row["htsno"],))
                    self.conn.execute("INSERT INTO tariff_fts(htsno, description) VALUES (?,?)",
                                      (row["htsno"], row["description"]))
            else:
                self.conn.execute("UPDATE tariff_lines SET last_seen=? WHERE htsno=?", (ts, row["htsno"]))

        for htsno in set(existing) - seen:
            stats["removed"] += 1
            changes.append((run_id, ts, htsno, "removed", None, existing[htsno]["general"], None))
            self.conn.execute("DELETE FROM tariff_lines WHERE htsno=?", (htsno,))
            self.conn.execute("DELETE FROM tariff_fts WHERE htsno=?", (htsno,))

        self.conn.executemany(
            """INSERT INTO tariff_changes(run_id, detected_at, htsno, change_type, field, old_value, new_value)
               VALUES (?,?,?,?,?,?,?)""",
            changes,
        )
        self.conn.commit()
        return stats

    def _total_lines(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM tariff_lines").fetchone()[0]

    # -- queries used by the agent tools -------------------------------------
    def get_line(self, htsno: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM tariff_lines WHERE htsno=?", (htsno,)).fetchone()
        return dict(r) if r else None

    def get_prefix(self, prefix: str, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tariff_lines WHERE htsno LIKE ? ORDER BY htsno LIMIT ?", (prefix + "%", limit)
        )
        return [dict(r) for r in rows]

    def ancestors(self, htsno: str) -> list[dict]:
        """Parent lines (4/6/8-digit) — rates are often only set on the 8-digit parent."""
        digits = htsno.replace(".", "")
        out = []
        for n in (4, 6, 8):
            if len(digits) > n:
                d = digits[:n]
                dotted = ".".join([d[:4]] + [d[i:i + 2] for i in range(4, n, 2)])
                r = self.get_line(dotted)
                if r:
                    out.append(r)
        return out

    def search(self, query: str, limit: int = 15) -> list[dict]:
        terms = [t for t in "".join(c if c.isalnum() else " " for c in query).split() if t]
        if not terms:
            return []
        fts_q = " OR ".join(f'"{t}"' for t in terms)
        rows = self.conn.execute(
            """SELECT l.* FROM tariff_fts f JOIN tariff_lines l ON l.htsno=f.htsno
               WHERE tariff_fts MATCH ? ORDER BY bm25(tariff_fts) LIMIT ?""",
            (fts_q, limit),
        )
        return [dict(r) for r in rows]

    def recent_changes(self, since_iso: str, htsno_prefix: str = "", limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            """SELECT * FROM tariff_changes WHERE detected_at >= ? AND htsno LIKE ?
               ORDER BY detected_at DESC, htsno LIMIT ?""",
            (since_iso, htsno_prefix + "%", limit),
        )
        return [dict(r) for r in rows]
