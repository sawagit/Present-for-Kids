"""Collector entry point.

    python -m collector.run --cadence hourly        # what the schedule runs
    python -m collector.run --sources usgs,gdacs
    python -m collector.run --all --dry-run         # fetch + validate, write nothing

Each source runs on its own: one failing source never stops the others.
With --strict the exit code is 1 if any source failed, so GitHub Actions
marks the run red and emails the owner (our only monitoring).

収集の入口。GitHub Actions のスケジュールから毎時・日次・週次で呼ばれる。
- ソースごとに独立して動かす。1 つが失敗しても他は止めない。
- --strict を付けると、失敗したソースがあれば終了コード 1 を返す。Actions の実行が
  赤くなり、持ち主に GitHub からメールが届く(これが唯一の監視の仕組み)。
- 実行の流れ: 保存済みデータの形式更新 → 各ソースの取得・保存 → status.json 更新
  → 分析ファイル(data/analysis/)の作り直し。
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

from . import analysis, store
from .http import SourceSkipped
from .schema import iso_utc
from .sources import SOURCES


@dataclass
class Context:
    """各ソースの fetch() に渡す設定。

    gdelt_slices: 既定 4(= 1 時間分)は手元やテストで軽く試すための値。
                  日次の本番は collect.yml で 96(= 24 時間分)を指定する。
    lookback_hours: ReliefWeb を何時間さかのぼるか。日次実行に対して 26 時間と
                  2 時間重ねて、実行時刻がずれても取りこぼさないようにする。
    """
    gdelt_slices: int = 4         # 15-minute files; 96 = one day
    lookback_hours: int = 26      # ReliefWeb window, overlaps a day for safety


def run_source(mod, ctx: Context) -> tuple[list[dict], dict]:
    """1 つのソースを取得し、(レコード, 実行結果) を返す。例外は外に出さない。

    実行結果には件数・分野別の内訳・最古と最新の日時などを入れ、status.json に
    残す。ソースが「準備中」(SourceSkipped)なら失敗ではなくスキップとして記録する。
    """
    started = time.monotonic()
    res = {"ok": False, "kind": mod.KIND, "cadence": mod.CADENCE, "count": 0, "error": None,
           "skipped": None}
    records: list[dict] = []
    try:
        records = mod.fetch(ctx)
        res["ok"] = True
        # ソースが絞り込みの統計(捨てた行数など)を持っていれば一緒に記録する。
        if getattr(mod, "LAST_STATS", None):
            res["stats"] = dict(mod.LAST_STATS)
        res["count"] = len(records)
        if mod.KIND == "events":
            res["by_category"] = dict(Counter(r["category"] for r in records))
            res["by_direction"] = dict(Counter(r["direction"] for r in records))
            res["with_coords"] = sum(1 for r in records if r["location"]["lat"] is not None)
            if records:
                times = sorted(r["occurred_at"] for r in records)
                res["oldest"], res["newest"] = times[0], times[-1]
    except SourceSkipped as e:
        res["skipped"] = str(e)
    except Exception as e:  # noqa: BLE001 - isolate every source
        # どんなエラーでも記録して次のソースへ進む(1 つの失敗で全体を止めない)。
        res["error"] = f"{type(e).__name__}: {e}"[:500]
        traceback.print_exc()
    res["seconds"] = round(time.monotonic() - started, 1)
    res["finished_at"] = iso_utc(datetime.now(timezone.utc))
    return records, res


def summary_markdown(results: dict, dry_run: bool) -> str:
    """実行結果を表にした Markdown を作る(Actions の実行画面の概要に表示される)。"""
    lines = [
        "## Collector run" + (" (dry run: nothing written)" if dry_run else ""),
        "",
        "| source | ok | records | with coords | new / updated | newest | seconds | error |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        nu = f"{r.get('new', '-')} / {r.get('updated', '-')}"
        lines.append(
            f"| {name} | {'✅' if r['ok'] else ('⏸️ skipped' if r['skipped'] else '❌')} | {r['count']} | {r.get('with_coords', '-')} "
            f"| {nu} | {r.get('newest', '-')} | {r['seconds']} | {(r['error'] or r['skipped'] or '').replace('|', '/')} |"
        )
    cats = Counter()
    for r in results.values():
        cats.update(r.get("by_category", {}))
    if cats:
        lines += ["", "Events by category: " + ", ".join(f"{k} {v}" for k, v in sorted(cats.items()))]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    """コマンドラインの入口。終了コード(0 = 正常、1 = --strict で失敗あり)を返す。"""
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

    # どのソースを動かすか: 全部 / 頻度で選ぶ(スケジュール実行用)/ 名前で指定。
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
    # 取得の前に、保存済みファイルを最新の形式に更新し、重複や古いニュース由来の
    # 出来事を整理する。こうしておけば形式を変えても手作業の移行が要らない。
    # dry-run(取得の確認だけ)のときはファイルに触らない。
    if not args.dry_run:
        up = store.upgrade_all(data_dir)
        print(f"--- upgrade stored events: {up}", file=sys.stderr)
    for name in names:
        mod = SOURCES[name]
        print(f"--- {name}", file=sys.stderr)
        records, res = run_source(mod, ctx)
        # 取得に成功したソースだけ保存する。失敗したソースの既存データは残る。
        if res["ok"] and not args.dry_run:
            if mod.KIND == "events":
                res.update(store.merge_events(data_dir, name, records))
            else:
                res.update(store.write_indicators(data_dir, name, records))
        results[name] = res
        print(f"    ok={res['ok']} count={res['count']} error={res['error']}", file=sys.stderr)

    if not args.dry_run:
        store.update_status(data_dir, results)
        try:
            print(f"--- analysis: {analysis.build(data_dir)}", file=sys.stderr)
        except Exception:  # noqa: BLE001 - never lose the collected data over this
            # 分析は付加的なもの。ここで失敗しても、集めたデータの保存は済んでいるので
            # 実行全体は失敗にしない(ログには残す)。
            traceback.print_exc()

    md = summary_markdown(results, args.dry_run)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(md)

    # スキップ(準備中)は失敗に数えない。
    failed = [n for n, r in results.items() if not r["ok"] and not r["skipped"]]
    return 1 if (failed and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
