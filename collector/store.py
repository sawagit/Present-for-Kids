"""Write normalized records as static JSON in the repository (Git = history).

Layout (all paths under the data directory):

    events/<source>/<YYYY-MM>.json     events, split by month of occurred_at
    indicators/<source>/<period>.json  indicators, one file per period
    status.json                        last run of every source

Files are JSON arrays with one record per line, so Git diffs stay readable
and the front end can fetch only the months it needs.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


def _dump_lines(records: list[dict]) -> str:
    if not records:
        return "[]\n"
    body = ",\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records)
    return "[\n" + body + "\n]\n"


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def merge_events(data_dir: Path, source: str, events: list[dict]) -> dict:
    """Upsert events by id. Returns counts {"new": n, "updated": n}."""
    by_month = defaultdict(list)
    for ev in events:
        by_month[ev["occurred_at"][:7]].append(ev)

    new = updated = 0
    for month, batch in by_month.items():
        path = data_dir / "events" / source / f"{month}.json"
        current = {e["id"]: e for e in _load(path)}
        for ev in batch:
            old = current.get(ev["id"])
            if old is None:
                new += 1
            elif old != ev:
                # Keep AI-filled fields (Japanese title, summaries) across re-fetches.
                for key in ("title", "summary"):
                    for lang, text in (old.get(key) or {}).items():
                        if text and not ev[key].get(lang):
                            ev[key][lang] = text
                if old != ev:
                    updated += 1
            current[ev["id"]] = ev
        records = sorted(current.values(), key=lambda e: (e["occurred_at"], e["id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_dump_lines(records), encoding="utf-8")
    return {"new": new, "updated": updated}


def write_indicators(data_dir: Path, source: str, indicators: list[dict]) -> dict:
    by_period = defaultdict(list)
    for ind in indicators:
        by_period[ind["period"]].append(ind)
    for period, batch in by_period.items():
        path = data_dir / "indicators" / source / f"{period}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        batch.sort(key=lambda i: i["id"])
        path.write_text(_dump_lines(batch), encoding="utf-8")
    return {"periods": sorted(by_period)}


def update_status(data_dir: Path, results: dict) -> None:
    path = data_dir / "status.json"
    status = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for source, res in results.items():
        prev = status.get(source, {})
        entry = dict(res)
        # Remember the last success even when this run failed.
        entry["last_success"] = res["finished_at"] if res["ok"] else prev.get("last_success")
        status[source] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
