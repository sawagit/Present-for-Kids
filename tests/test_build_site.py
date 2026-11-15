import json
import sys
import tempfile
import unittest
from pathlib import Path

# scripts/ はパッケージではないので、import できるよう検索パスに足す
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_site  # noqa: E402


class BuildSiteTest(unittest.TestCase):
    """scripts/build_site.py のテスト(ネットワーク不要、一時フォルダの中だけで動く)。"""

    def test_manifest_lists_months_and_periods(self):
        """manifest に月・年が昇順で載り、サイトとデータがコピーされ、.gitkeep は除かれることを確かめる。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            site = tmp / "site"
            site.mkdir()
            (site / "index.html").write_text("<!doctype html>", encoding="utf-8")
            data = tmp / "data"
            for rel in ("events/usgs/2026-11.json", "events/usgs/2026-10.json",
                        "events/gdacs/2026-11.json", "indicators/unhcr/2025.json"):
                p = data / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("[]\n", encoding="utf-8")
            (data / ".gitkeep").write_text("", encoding="utf-8")
            out = tmp / "out"

            manifest = build_site.build(site, data, out)

            self.assertEqual(manifest["events"], {"gdacs": ["2026-11"], "usgs": ["2026-10", "2026-11"]})
            self.assertEqual(manifest["indicators"], {"unhcr": ["2025"]})
            on_disk = json.loads((out / "data" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(on_disk["events"], manifest["events"])
            self.assertTrue((out / "index.html").exists())
            self.assertTrue((out / "data" / "events" / "usgs" / "2026-10.json").exists())
            self.assertTrue((out / ".nojekyll").exists())
            self.assertFalse((out / "data" / ".gitkeep").exists())


if __name__ == "__main__":
    unittest.main()
