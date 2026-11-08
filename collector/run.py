"""Collector entry point.

    python -m collector.run --cadence hourly        # what the schedule runs
    python -m collector.run --sources usgs,gdacs
    python -m collector.run --all --dry-run         # fetch + validate, write nothing

Each source runs on its own: one failing source never stops the others.
With --strict the exit code is 1 if any source failed, so GitHub Actions
marks the run red and emails the owner (our only monitoring).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .schema import iso_utc
from .sources import SOURCES


@dataclass
class Context:
    gdelt_slices: int = 4         # 15-minute files; 96 = one day
    lookback_hours: int = 26      # ReliefWeb window, overlaps a day for safety


def run_source(mod, ctx: Context) -> tuple[list[dict], dict]:
    started = time.monotonic()
    res = {"ok": False, "kind": mod.KIND, "cadence": mod.CADENCE, "count": 0, "error": None}
    records: list[dict] = []
    try:
        records = mod.fetch(ctx)
        res["ok"] = True
        res["count"] = len(records)
        if mod.KIND == "events":
            res["by_category"] = dict(Counter(r["category"] for r in records))
            res["by_direction"] = dict(Counter(r["direction"] for r in records))
            res["with_coords"] = sum(1 for r in records if r["location"]["lat"] is not None)
            if records:
                times = sorted(r["occurred_at"] for r in records)
                res["oldest"], res["newest"] = times[0], times[-1]
    except Exception as e:  # noqa: BLE001 - isolate every source
        res["error"] = f"{type(e).__name__}: {e}"[:500]
        traceback.print_exc()
    res["seconds"] = round(time.monotonic() - started, 1)
    res["finished_at"] = iso_utc(datetime.now(timezone.utc))
    return records, res


def summary_markdown(results: dict, dry_run: bool) -> str:
    lines = [
        "## Collector run" + (" (dry run: nothing written)" if dry_run else ""),
        "",
        "| source | ok | records | with coords | new / updated | newest | seconds | error |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        nu = f"{r.get('new', '-')} / {r.get('updated', '-')}"
        lines.append(
            f"| {name} | {'✅' if r['ok'] else '❌'} | {r['count']} | {r.get('with_coords', '-')} "
            f"| {nu} | {r.get('newest', '-')} | {r['seconds']} | {(r['error'] or '').replace('|', '/')} |"
        )
    cats = Counter()
    for r in results.values():
        cats.update(r.get("by_category", {}))
    if cats:
        lines += ["", "Events by category: " + ", ".join(f"{k} {v}" for k, v in sorted(cats.items()))]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    pick = ap.add_mutually_exclusive_group(required=True)
    pick.add_argument("--sources", help="comma separated: " + ",".join(SOURCES))
    pick.add_argument("--cadence", choices=["hourly", "daily", "weekly"])
    pick.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="data", help="data directory (default: data)")
    ap.add_argument("--dry-run", action="store_true", help="fetch and validate only")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any source failed")
    ap.add_argument("--gdelt-slices", type=int, default=int(os.environ.get("GDELT_SLICES", 4)))
    ap.add_argument("--lookback-hours", type=int, default=26)
    args = ap.parse_args(argv)

    if args.all:
        names = list(SOURCES)
    elif args.cadence:
        names = [n for n, m in SOURCES.items() if m.CADENCE == args.cadence]
    else:
        names = [n.strip() for n in args.sources.split(",") if n.strip()]
        unknown = [n for n in names if n not in SOURCES]
        if unknown:
            ap.error(f"unknown source(s): {', '.join(unknown)}")

    ctx = Context(gdelt_slices=args.gdelt_slices, lookback_hours=args.lookback_hours)
    data_dir = Path(args.out)
    results = {}
    for name in names:
        mod = SOURCES[name]
        print(f"--- {name}", file=sys.stderr)
        records, res = run_source(mod, ctx)
        if res["ok"] and not args.dry_run:
            if mod.KIND == "events":
                res.update(store.merge_events(data_dir, name, records))
            else:
                res.update(store.write_indicators(data_dir, name, records))
        results[name] = res
        print(f"    ok={res['ok']} count={res['count']} error={res['error']}", file=sys.stderr)

    if not args.dry_run:
        store.update_status(data_dir, results)

    md = summary_markdown(results, args.dry_run)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(md)

    failed = [n for n, r in results.items() if not r["ok"]]
    return 1 if (failed and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
