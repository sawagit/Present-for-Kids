"""Offline tests: parse recorded/handmade source responses. No network needed.

Run:  python -m unittest discover -s tests
"""

import csv
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from collector import run, store
from collector.schema import CATEGORIES, validate_event
from collector.sources import gdacs, gdelt, reliefweb, unhcr, usgs

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class UsgsTest(unittest.TestCase):
    def test_parse(self):
        evs = usgs.parse(load("usgs.json"))
        self.assertEqual([e["id"] for e in evs], ["usgs:us7000abcd", "usgs:us7000abce"])
        e = evs[0]
        self.assertEqual(e["category"], "disaster_climate")
        self.assertEqual(e["severity"]["level"], "medium")  # PAGER yellow
        self.assertEqual(e["location"], {"lat": -8.99, "lon": 110.12, "place": "120 km SSW of Example, Indonesia", "country_iso3": None})
        self.assertEqual(e["confidence"], "high")
        self.assertEqual(evs[1]["confidence"], "medium")  # automatic, not reviewed
        self.assertTrue(e["occurred_at"].endswith("Z"))
        self.assertEqual(e["title"]["ja"], None)


class GdacsTest(unittest.TestCase):
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

    def test_old_dated_events_dropped(self):
        rows = self.rows()
        rows[0][1] = "20251107"  # same clash, but dated a year before it was seen
        types = {e["type"]: e for e in gdelt.parse_rows(rows)}
        self.assertEqual(types["cameo_190"]["severity"]["value"], 10)

    def test_zip_reader_and_lastupdate(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("20261107101500.export.CSV", (FIX / "gdelt_export.tsv").read_text())
        rows = list(gdelt._rows_from_zip(buf.getvalue()))
        self.assertEqual(len(rows), 7)
        lu = ("123 abc http://data.gdeltproject.org/gdeltv2/20261108081500.export.CSV.zip\n"
              "456 def http://data.gdeltproject.org/gdeltv2/20261108081500.mentions.CSV.zip\n")
        self.assertEqual(gdelt.latest_slice(lu).strftime("%Y%m%d%H%M"), "202611080815")


class ReliefwebTest(unittest.TestCase):
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


class ReliefwebRssTest(unittest.TestCase):
    def test_parse_rss(self):
        evs = reliefweb.parse_rss((FIX / "reliefweb_rss.xml").read_bytes())
        self.assertEqual(len(evs), 2)  # item without date skipped
        chol, refu = evs
        self.assertEqual(chol["id"], "reliefweb:4100010")
        self.assertEqual(chol["category"], "public_health")
        self.assertEqual(chol["location"]["place"], "Sudan")
        self.assertEqual(chol["occurred_at"], "2026-11-14T10:20:30Z")
        self.assertNotIn("body", json.dumps(chol))  # description never stored
        self.assertEqual(refu["tags"], ["displacement"])
        self.assertEqual(refu["location"]["place"], "Chad")

    def test_rss_used_without_appname(self):
        raw = (FIX / "reliefweb_rss.xml").read_bytes()
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(reliefweb.http, "get", return_value=raw) as get:
            evs = reliefweb.fetch(run.Context())
        get.assert_called_once_with(reliefweb.RSS_URL)
        self.assertEqual(len(evs), 2)


class UnhcrTest(unittest.TestCase):
    def test_parse(self):
        inds = unhcr.parse(load("unhcr.json")["items"], 2025)
        names = {i["indicator"]: i["value"] for i in inds}
        self.assertEqual(names["refugees_by_origin"], 5500000)
        self.assertEqual(names["asylum_seekers_by_origin"], 300000)
        self.assertEqual(names["returned_idps_by_origin"], 0)
        self.assertNotIn("returned_refugees_by_origin", names)  # "-" = no data
        self.assertTrue(all(i["area"]["iso3"] == "AFG" for i in inds))


class SchemaTest(unittest.TestCase):
    def test_all_categories_reachable_contract(self):
        self.assertEqual(len(CATEGORIES), 8)

    def test_rejects_event_without_url(self):
        ev = usgs.parse(load("usgs.json"))[0]
        ev["urls"] = []
        with self.assertRaises(ValueError):
            validate_event(ev)


class StoreTest(unittest.TestCase):
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


class RunTest(unittest.TestCase):
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
