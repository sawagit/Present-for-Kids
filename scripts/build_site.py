"""Assemble the static site: site/ + data/ + a manifest, into one folder.

    python scripts/build_site.py                 # writes _site/
    python -m http.server -d _site 8000          # preview at http://localhost:8000

The browser cannot list directories on GitHub Pages, so the manifest tells
it which monthly event files and indicator periods exist. Standard library
only, like the collector.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build_manifest(data_dir: Path) -> dict:
    manifest = {
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "events": {},
        "indicators": {},
    }
    for kind in ("events", "indicators"):
        base = data_dir / kind
        if not base.is_dir():
            continue
        for src in sorted(p for p in base.iterdir() if p.is_dir()):
            names = sorted(p.stem for p in src.glob("*.json"))
            if names:
                manifest[kind][src.name] = names
    return manifest


def build(site_dir: Path, data_dir: Path, out_dir: Path) -> dict:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(site_dir, out_dir)
    out_data = out_dir / "data"
    if data_dir.is_dir():
        shutil.copytree(data_dir, out_data, ignore=shutil.ignore_patterns(".gitkeep"))
    else:
        out_data.mkdir()
    manifest = build_manifest(data_dir)
    (out_data / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    # GitHub Pages: serve files as-is (no Jekyll processing).
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return manifest


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site", type=Path, default=ROOT / "site")
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=ROOT / "_site")
    args = ap.parse_args(argv)
    manifest = build(args.site, args.data, args.out)
    n = sum(len(v) for v in manifest["events"].values())
    print(f"built {args.out} ({n} event files, sources: {', '.join(manifest['events'])})")


if __name__ == "__main__":
    main()
