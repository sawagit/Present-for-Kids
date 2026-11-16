"""Offline tests: parse recorded/handmade source responses. No network needed.

Run:  python -m unittest discover -s tests

通信なしで動くテスト。記録しておいた応答や手作りのデータを使って、各ソースの
変換・保存・分析が正しく動くかを確かめる。CI でプッシュのたびに実行される。
"""

import csv
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from collector import analysis, geo, run, store
from collector.schema import CATEGORIES, upgrade_event, validate_event
from collector.sources import gdacs, gdelt, reliefweb, unhcr, usgs

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class UsgsTest(unittest.TestCase):
    """USGS の地震データが共通形式に正しく変換されるか(深刻度・信頼度・国の判定)。"""

    def test_parse(self):
        evs = usgs.parse(load("usgs.json"))
        self.assertEqual([e["id"] for e in evs], ["usgs:us7000abcd", "usgs:us7000abce"])
        e = evs[0]
        self.assertEqual(e["category"], "disaster_climate")
        self.assertEqual(e["severity"]["level"], "medium")  # PAGER yellow
        self.assertEqual(e["location"], {"lat": -8.99, "lon": 110.12, "place": "120 km SSW of Example, Indonesia", "country_iso3": "IDN",
                                         "region": "Asia", "subregion": "South-eastern Asia"})
        self.assertEqual(e["confidence"], "high")
        self.assertEqual(evs[1]["confidence"], "medium")  # automatic, not reviewed
        self.assertTrue(e["occurred_at"].endswith("Z"))
        self.assertEqual(e["title"]["ja"], None)


class GdacsTest(unittest.TestCase):
    """GDACS の災害警報の変換。日付が壊れた行は落とさずに飛ばせるか。"""

    def test_parse(self):
        evs = gdacs.parse(load("gdacs.json"))
        self.assertEqual(len(evs), 2)  # bad date skipped, not crashed
        tc = evs[0]
        self.assertEqual(tc["id"], "gdacs:TC1001234")
        self.assertEqual(tc["type"], "tropical_cyclone")
        self.assertEqual(tc["severity"]["level"], "high")
        self.assertEqual(tc["occurred_at"], "2026-11-05T00:00:00Z")
        self.assertEqual(tc["attrs"]["affected_iso3"], ["BGD", "IND"])
        fl = evs[1]
        self.assertIn("report.aspx?eventtype=FL&eventid=1100555", fl["urls"][0])
        self.assertEqual(fl["severity"]["level"], "low")


class GdeltTest(unittest.TestCase):
    """GDELT の絞り込み・重複のまとめ・古い出来事の除外・ID の安定性・国別の上限。"""

    def rows(self):
        text = (FIX / "gdelt_export.tsv").read_text(encoding="utf-8")
        return list(csv.reader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE))

    def test_filter_merge_and_improvements(self):
        evs = gdelt.parse_rows(self.rows())
        by_type = {e["type"]: e for e in evs}
        self.assertEqual(set(by_type), {"cameo_190", "cameo_073", "cameo_0871"})
        clash = by_type["cameo_190"]
        self.assertEqual(clash["severity"]["value"], 50)          # 40 + 10 merged
        self.assertEqual(len(clash["urls"]), 2)
        self.assertEqual(clash["confidence"], "low")
        self.assertEqual(clash["category"], "conflict_security")
        self.assertEqual(clash["location"]["lat"], 13.63)          # rounded for privacy
        self.assertEqual(by_type["cameo_073"]["direction"], "improving")
        self.assertEqual(by_type["cameo_0871"]["direction"], "turning_point")
        self.assertEqual(evs[0]["type"], "cameo_190")             # ranked by articles
        self.assertEqual(clash["location"]["country_iso3"], "SDN")  # FIPS SU -> ISO3
        self.assertEqual(clash["location"]["subregion"], "Northern Africa")

    def test_stable_id_across_runs(self):
        rows = self.rows()
        first = {e["type"]: e["id"] for e in gdelt.parse_rows(rows)}
        rows.reverse()  # another run sees the mentions in another order
        rows[0][0] = "999"
        second = {e["type"]: e["id"] for e in gdelt.parse_rows(rows)}
        self.assertEqual(first, second)

    def test_per_country_cap(self):
        with mock.patch.object(gdelt, "MAX_PER_COUNTRY", 1):
            evs = gdelt.parse_rows(self.rows())
        self.assertEqual(len(evs), 1)  # fixture rows are all coded SU
        self.assertEqual(evs[0]["type"], "cameo_190")

    def test_old_dated_events_dropped(self):
        rows = self.rows()
        rows[0][1] = "20251107"  # same clash, but dated a year before it was seen
        types = {e["type"]: e for e in gdelt.parse_rows(rows)}
        self.assertEqual(types["cameo_190"]["severity"]["value"], 10)
        rows[1][1] = "20261103"  # 4 days before it was seen: also dropped (limit 3)
        self.assertNotIn("cameo_190", {e["type"] for e in gdelt.parse_rows(rows)})

    def test_zip_reader_and_lastupdate(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("20261107101500.export.CSV", (FIX / "gdelt_export.tsv").read_text())
        rows = list(gdelt._rows_from_zip(buf.getvalue()))
        self.assertEqual(len(rows), 7)
        lu = ("123 abc http://data.gdeltproject.org/gdeltv2/20261108081500.export.CSV.zip\n"
              "456 def http://data.gdeltproject.org/gdeltv2/20261108081500.mentions.CSV.zip\n")
        self.assertEqual(gdelt.latest_slice(lu).strftime("%Y%m%d%H%M"), "202611080815")

    def fake_get(self, slice_ok):
        """lastupdate.txt と zip を返す偽の http.get。slice_ok() が False の間は全スライス 404。"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("x.export.CSV", (FIX / "gdelt_export.tsv").read_text())
        lu = b"1 a http://data.gdeltproject.org/gdeltv2/20261108081500.export.CSV.zip\n"

        def get(url, **kw):
            if url == gdelt.LASTUPDATE_URL:
                return lu
            if not slice_ok():
                raise gdelt.http.FetchError(f"HTTP 404 for {url}: ")
            return buf.getvalue()
        return get

    def test_fetch_retries_when_every_slice_is_missing(self):
        # 2026-11-15 の CI 失敗の再現: 1 回目は全スライス 404、待ってやり直すと取れる。
        calls = {"round": 0}
        waits = []

        def slice_ok():
            return calls["round"] >= 1

        def sleep(sec):
            waits.append(sec)
            calls["round"] += 1

        ctx = mock.Mock(gdelt_slices=2)
        with mock.patch.object(gdelt.http, "get", self.fake_get(slice_ok)):
            evs = gdelt.fetch(ctx, sleep=sleep)
        self.assertEqual(waits, [gdelt.ROUND_WAIT_SECONDS])
        self.assertEqual(gdelt.LAST_STATS["slices_ok"], 2)
        self.assertIsInstance(evs, list)

    def test_fetch_fails_after_all_retries(self):
        waits = []
        ctx = mock.Mock(gdelt_slices=2)
        with mock.patch.object(gdelt.http, "get", self.fake_get(lambda: False)):
            with self.assertRaises(gdelt.http.FetchError):
                gdelt.fetch(ctx, sleep=waits.append)
        self.assertEqual(len(waits), gdelt.ROUND_RETRIES)


class ReliefwebTest(unittest.TestCase):
    """ReliefWeb レポートの分野の判定と、難民・移動タグ。"""

    def test_parse(self):
        evs = reliefweb.parse(load("reliefweb.json"))
        self.assertEqual(len(evs), 2)  # map-only item dropped
        sit, chol = evs
        self.assertEqual(sit["category"], "human_rights")
        self.assertEqual(sit["location"]["country_iso3"], "SDN")
        self.assertEqual(sit["occurred_at"], "2026-11-06T00:00:00Z")
        self.assertIn("/report/sudan/", sit["urls"][0])
        self.assertEqual(sit["direction"], "neutral")
        self.assertEqual(chol["category"], "public_health")

    def test_displacement_tag(self):
        doc = load("reliefweb.json")
        doc["data"][0]["fields"]["title"] = "Refugees arrive in Chad"
        self.assertEqual(reliefweb.parse(doc)[0]["tags"], ["displacement"])

    def test_query_shape(self):
        from datetime import datetime, timezone
        q = reliefweb.query(datetime(2026, 11, 7, tzinfo=timezone.utc))
        self.assertEqual(q["filter"]["value"]["from"], "2026-11-07T00:00:00+00:00")


class ReliefwebSkipTest(unittest.TestCase):
    """appname が未登録のとき、失敗ではなくスキップになるか。"""

    def test_skipped_without_appname_is_not_a_failure(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", {}, clear=True):
            code = run.main(["--sources", "reliefweb", "--out", d, "--strict"])
            status = json.loads((Path(d) / "status.json").read_text())
        self.assertEqual(code, 0)
        self.assertIn("RELIEFWEB_APPNAME", status["reliefweb"]["skipped"])


class UnhcrTest(unittest.TestCase):
    """UNHCR の統計の変換。"-"(データなし)を 0 と区別できるか。"""

    def test_parse(self):
        inds = unhcr.parse(load("unhcr.json")["items"], 2025)
        names = {i["indicator"]: i["value"] for i in inds}
        self.assertEqual(names["refugees_by_origin"], 5500000)
        self.assertEqual(names["asylum_seekers_by_origin"], 300000)
        self.assertEqual(names["returned_idps_by_origin"], 0)
        self.assertNotIn("returned_refugees_by_origin", names)  # "-" = no data
        self.assertTrue(all(i["area"]["iso3"] == "AFG" for i in inds))


class SchemaTest(unittest.TestCase):
    """共通形式の約束(8 分野、出典 URL 必須)が守られるか。"""

    def test_all_categories_reachable_contract(self):
        self.assertEqual(len(CATEGORIES), 8)

    def test_rejects_event_without_url(self):
        ev = usgs.parse(load("usgs.json"))[0]
        ev["urls"] = []
        with self.assertRaises(ValueError):
            validate_event(ev)


class StoreTest(unittest.TestCase):
    """保存の upsert が何度実行しても同じ結果になり、日本語訳を失わないか。"""

    def test_merge_is_idempotent_and_keeps_translations(self):
        evs = usgs.parse(load("usgs.json"))
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertEqual(store.merge_events(d, "usgs", evs), {"new": 2, "updated": 0})
            path = next((d / "events" / "usgs").glob("*.json"))
            saved = json.loads(path.read_text())
            saved[0]["title"]["ja"] = "インドネシアで地震"
            path.write_text(json.dumps(saved))
            again = usgs.parse(load("usgs.json"))
            self.assertEqual(store.merge_events(d, "usgs", again), {"new": 0, "updated": 0})
            self.assertEqual(json.loads(path.read_text())[0]["title"]["ja"], "インドネシアで地震")


class GeoTest(unittest.TestCase):
    """国コード・地名から国と地域を正しく求められるか(海域は推測しないこと)。"""

    def test_lookups(self):
        self.assertEqual(geo.iso3_from_fips("UK"), "GBR")
        self.assertEqual(geo.iso3_from_fips("GZ"), "PSE")
        self.assertEqual(geo.iso3_from_fips("RI"), "SRB")
        self.assertEqual(geo.iso3_from_place("10 km N of Ridgecrest, CA"), "USA")
        self.assertEqual(geo.iso3_from_place("5 km E of Tbilisi, Georgia"), "GEO")
        self.assertEqual(geo.iso3_from_place("Fiji region"), "FJI")
        self.assertEqual(geo.iso3_from_place("south of the Kermadec Islands"), "NZL")
        self.assertEqual(geo.iso3_from_place("off the west coast of northern Sumatra"), "IDN")
        self.assertIsNone(geo.iso3_from_place("Northern Mid-Atlantic Ridge"))
        self.assertEqual(geo.country("JPN")["name_ja"], "日本")
        self.assertEqual(geo.regions("SSD"), ("Africa", "Eastern Africa"))
        for region in {geo.regions(c["iso3"])[1] for c in json.loads(geo.TABLE.read_text())} - {None}:
            self.assertIn(region, geo.REGION_JA)


class UpgradeTest(unittest.TestCase):
    """古い形式(バージョン 1)の保存データが自動で更新され、重複がまとまるか。"""

    def v1(self, ev):
        """Turn a freshly parsed event back into how version 1 stored it."""
        ev = json.loads(json.dumps(ev))
        ev["schema"] = 1
        del ev["location"]["region"], ev["location"]["subregion"]
        ev["location"]["country_iso3"] = None
        return ev

    def test_v1_files_are_upgraded_and_gdelt_duplicates_merged(self):
        evs = gdelt.parse_rows(GdeltTest().rows())
        clash = next(e for e in evs if e["type"] == "cameo_190")
        old_a, old_b = self.v1(clash), self.v1(clash)
        old_a["id"], old_b["id"] = "gdelt:1", "gdelt:2"  # v1 ids differed per run
        old_b["urls"] = ["https://news.example/z"]
        old_b["title"]["ja"] = "衝突"
        stale = self.v1(clash)
        stale["id"], stale["occurred_at"] = "gdelt:3", "2026-10-01T00:00:00Z"
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            path = d / "events" / "gdelt" / "2026-11.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps([old_a, old_b]))
            (d / "events" / "gdelt" / "2026-10.json").write_text(json.dumps([stale]))
            self.assertEqual(store.upgrade_all(d), {"files": 2, "removed": 2})
            saved = json.loads(path.read_text())
            self.assertFalse((d / "events" / "gdelt" / "2026-10.json").exists())
            self.assertEqual(store.upgrade_all(d), {"files": 0, "removed": 0})  # idempotent
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["id"], clash["id"])
        self.assertEqual(saved[0]["schema"], 2)
        self.assertEqual(saved[0]["location"]["country_iso3"], "SDN")
        self.assertIn("https://news.example/z", saved[0]["urls"])
        self.assertEqual(saved[0]["title"]["ja"], "衝突")

    def test_usgs_upgrade(self):
        ev = upgrade_event(self.v1(usgs.parse(load("usgs.json"))[0]))
        self.assertEqual(ev["location"]["country_iso3"], "IDN")


class AnalysisTest(unittest.TestCase):
    """国 × 週の集計、つながり候補、同じ地震の二重計上の防止。"""

    def test_grid_candidates_and_same_quake(self):
        from datetime import datetime, timezone
        quakes = usgs.parse(load("usgs.json"))
        twin = json.loads(json.dumps(quakes[0]))  # GDACS reports the same quake
        twin.update(id="gdacs:EQ1", source="gdacs")
        twin["location"]["lat"] += 0.3
        clash = next(e for e in gdelt.parse_rows(GdeltTest().rows()) if e["type"] == "cameo_190")
        aid = json.loads(json.dumps(clash))
        aid.update(id="reliefweb:1", source="reliefweb", category="public_health",
                   direction="neutral", occurred_at=clash["occurred_at"].replace("07T", "06T"))
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            store.merge_events(d, "usgs", quakes)
            store.merge_events(d, "gdacs", [twin])
            store.merge_events(d, "gdelt", [clash])
            store.merge_events(d, "reliefweb", [aid])
            res = analysis.build(d, now=datetime(2026, 11, 15, tzinfo=timezone.utc))
            grid = json.loads((d / "analysis" / "country_weeks.json").read_text())
            cands = json.loads((d / "analysis" / "link_candidates.json").read_text())
        self.assertEqual(grid["same_event"], {"gdacs:EQ1": quakes[0]["id"]})
        idn = [c for c in grid["cells"] if c["country_iso3"] == "IDN"]
        self.assertEqual(sum(c["total"] for c in idn), len([q for q in quakes if q["location"]["country_iso3"] == "IDN"]))
        self.assertEqual(res["candidates"], 1)
        cand = cands["candidates"][0]
        self.assertEqual((cand["country_iso3"], cand["week"]), ("SDN", "2026-W45"))
        self.assertEqual(cand["categories"], ["conflict_security", "public_health"])
        self.assertEqual(cand["non_news_categories"], 1)


class RunTest(unittest.TestCase):
    """1 つのソースが失敗しても他のソースの収集が続くか。"""

    def test_one_failing_source_does_not_stop_others(self):
        def boom(ctx):
            raise RuntimeError("down")
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(usgs, "fetch", lambda ctx: usgs.parse(load("usgs.json"))), \
                mock.patch.object(gdacs, "fetch", boom):
            code = run.main(["--sources", "gdacs,usgs", "--out", d, "--strict"])
            status = json.loads((Path(d) / "status.json").read_text())
        self.assertEqual(code, 1)
        self.assertFalse(status["gdacs"]["ok"])
        self.assertIn("down", status["gdacs"]["error"])
        self.assertTrue(status["usgs"]["ok"])
        self.assertEqual(status["usgs"]["new"], 2)


if __name__ == "__main__":
    unittest.main()
