import copy
import json

import pytest

from backend.tariffs import crawler, tools
from backend.tariffs.store import TariffStore


def rec(htsno, desc, general="", special="", other="", indent=1, footnotes=None, units=None):
    return {"htsno": htsno, "statisticalSuffix": "", "description": desc, "indent": indent,
            "units": units or [], "general": general, "special": special, "other": other,
            "footnotes": footnotes or [], "superior": None, "quotaQuantity": None, "additionalDuties": None}


DAY1 = {
    74: [rec("7403", "Refined copper and copper alloys, unwrought:", indent=0),
         rec("", "Refined copper:", indent=1),
         rec("7403.11.00", "Cathodes and sections of cathodes", "1%", "Free (A,AU,BH)", "6%", 2, units=["kg"])],
    85: [rec("8507", "Electric storage batteries", indent=0),
         rec("8507.60.00", "Lithium-ion batteries", "3.4%", "Free (A,AU)", "40%", 1,
             footnotes=[{"columns": ["general"], "value": "See 9903.88.03.", "type": "endnote"}]),
         rec("8507.60.00.20", "Of a kind used as the primary source of power for electric vehicles", indent=2, units=["No."])],
}


class FakeClient:
    delay = 0

    def __init__(self, data, fail=()):
        self.data, self.fail = data, set(fail)

    def export_chapter(self, ch):
        if ch in self.fail:
            raise RuntimeError("HTTP 500")
        return self.data.get(ch, [])


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    monkeypatch.setenv("TARIFF_DB_PATH", path)
    tools._store = None
    return path


def test_initial_load_no_change_noise(db):
    res = crawler.run_crawl(db, chapters=[74, 85], client=FakeClient(DAY1))
    assert res["status"] == "success"
    assert res["lines_seen"] == 5  # un-numbered "Refined copper:" heading skipped
    assert res["added"] == 0  # first-ever load is a baseline, not 30k "added" changes
    s = TariffStore(db)
    assert s.get_line("7403.11.00")["general"] == "1%"


def test_detects_rate_change_addition_removal(db):
    crawler.run_crawl(db, chapters=[74, 85], client=FakeClient(DAY1))
    day2 = copy.deepcopy(DAY1)
    day2[85][1]["general"] = "25%"                         # rate hike
    day2[85].append(rec("8507.80.00", "Other storage batteries", "3.5%"))  # new line
    day2[74] = [r for r in day2[74] if r["htsno"] != "7403.11.00"]          # removed
    res = crawler.run_crawl(db, chapters=[74, 85], client=FakeClient(day2))
    assert (res["added"], res["modified"], res["removed"]) == (1, 1, 1)

    s = TariffStore(db)
    ch = {(c["htsno"], c["change_type"]): c for c in s.recent_changes("2000-01-01")}
    assert ch[("8507.60.00", "modified")]["old_value"] == "3.4%"
    assert ch[("8507.60.00", "modified")]["new_value"] == "25%"
    assert ("8507.80.00", "added") in ch and ("7403.11.00", "removed") in ch


def test_failed_or_empty_chapter_keeps_snapshot(db):
    crawler.run_crawl(db, chapters=[74, 85], client=FakeClient(DAY1))
    res = crawler.run_crawl(db, chapters=[74, 85], client=FakeClient({74: DAY1[74]}, fail=[]))  # 85 empty
    assert res["status"] == "partial" and res["failed_chapters"] == [85] and res["removed"] == 0
    res = crawler.run_crawl(db, chapters=[74, 85], client=FakeClient(DAY1, fail=[74]))
    assert res["status"] == "partial" and res["failed_chapters"] == [74]
    assert TariffStore(db).get_line("7403.11.00") is not None


def test_agent_tools(db):
    crawler.run_crawl(db, chapters=[74, 85], client=FakeClient(DAY1))
    out = json.loads(tools.lookup_hts_code.invoke({"hts_code": "8507600020"}))
    assert out["match"]["hts"] == "8507.60.00.20"
    assert out["parents"][-1]["general_rate"] == "3.4%"  # rate inherited from 8-digit parent
    assert "9903.88.03" in out["parents"][-1]["footnotes"][0]

    out = json.loads(tools.search_tariffs.invoke({"product_description": "copper cathodes"}))
    assert out["results"][0]["hts"] == "7403.11.00"

    day2 = copy.deepcopy(DAY1); day2[85][1]["general"] = "25%"
    crawler.run_crawl(db, chapters=[74, 85], client=FakeClient(day2))
    out = json.loads(tools.recent_tariff_changes.invoke({"days": 1, "hts_prefix": "85"}))
    assert out["count"] == 1 and out["changes"][0]["new_value"] == "25%"


def test_next_run_is_within_a_day(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from backend.tariffs.scheduler import seconds_until_next_run
    monkeypatch.setenv("TARIFF_CRAWL_TIME", "06:00")
    monkeypatch.setenv("TARIFF_CRAWL_TZ", "America/New_York")
    tz = ZoneInfo("America/New_York")
    assert seconds_until_next_run(datetime(2026, 10, 27, 5, 0, tzinfo=tz)) == 3600
    assert seconds_until_next_run(datetime(2026, 10, 27, 7, 0, tzinfo=tz)) == 23 * 3600


async def test_dashboard_endpoints(db, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from backend.api.main import app
    from backend.tariffs import scheduler

    real = crawler.run_crawl
    monkeypatch.setattr(scheduler, "run_crawl",
                        lambda chapters=None, on_progress=None: real(db, chapters=[74, 85], client=FakeClient(DAY1), on_progress=on_progress))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        s = (await client.get("/api/tariffs/status")).json()
        assert s["last_run"] is None and s["total_lines"] == 0 and not s["running"]
        assert (await client.post("/api/tariffs/sync", json={"chapters": [77]})).status_code == 422
        r = await client.post("/api/tariffs/sync", json={"chapters": [74, 85]})
        assert r.status_code == 200 and r.json()["running"]
        assert (await client.post("/api/tariffs/sync")).status_code == 409  # one at a time
        await scheduler.controller.wait()
        s = (await client.get("/api/tariffs/status")).json()
        assert s["last_run"]["status"] == "success" and s["total_lines"] == 5 and s["chapters_loaded"] == 2

        day2 = copy.deepcopy(DAY1); day2[85][1]["general"] = "25%"
        real(db, chapters=[74, 85], client=FakeClient(day2))
        ch = (await client.get("/api/tariffs/changes?days=1")).json()["changes"]
        assert ch[0]["htsno"] == "8507.60.00" and ch[0]["new_value"] == "25%"
        assert ch[0]["description"] == "Lithium-ion batteries"
        assert (await client.get("/api/tariffs/status")).json()["changes_7d"] == 1
