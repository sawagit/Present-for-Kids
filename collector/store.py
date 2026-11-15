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
from datetime import datetime
from pathlib import Path

from .schema import upgrade_event


def _dump_lines(records: list[dict]) -> str:
    if not records:
        return "[]\n"
    body = ",\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records)
    return "[\n" + body + "\n]\n"


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _keep_translations(old: dict, ev: dict) -> None:
    """Keep AI-filled fields (Japanese title, summaries) across re-fetches."""
    for key in ("title", "summary"):
        for lang, text in (old.get(key) or {}).items():
            if text and not ev[key].get(lang):
                ev[key][lang] = text


def _combine_news(old: dict, new: dict) -> dict:
    """Two sightings of one news-coded event (GDELT): keep the evidence of both."""
    base, other = (new, old) if (new["severity"]["value"] or 0) >= (old["severity"]["value"] or 0) else (old, new)
    out = json.loads(json.dumps(base))
    out["urls"] = list(dict.fromkeys(base["urls"] + other["urls"]))[:5]
    ids = base["attrs"].get("merged_event_ids", []) + other["attrs"].get("merged_event_ids", [])
    out["attrs"]["merged_event_ids"] = list(dict.fromkeys(ids))[:20]
    out["updated_at"] = max(filter(None, [old.get("updated_at"), new.get("updated_at")]), default=None)
    _keep_translations(other, out)
    return out


def _stale_news(ev: dict, max_age_days: int) -> bool:
    """A GDELT event dated long before the news that reported it (old data)."""
    if ev["source"] != "gdelt" or not ev.get("updated_at"):
        return False
    occurred = datetime.fromisoformat(ev["occurred_at"][:10])
    seen = datetime.fromisoformat(ev["updated_at"][:10])
    return (seen - occurred).days > max_age_days


def _load_events(path: Path) -> dict[str, dict]:
    """Load a month file, upgraded to the current schema, duplicates merged."""
    out: dict[str, dict] = {}
    for ev in _load(path):
        ev = upgrade_event(ev)
        old = out.get(ev["id"])
        out[ev["id"]] = _combine_news(old, ev) if old else ev
    return out


def upgrade_all(data_dir: Path) -> dict:
    """Rewrite stored event files to the current schema and drop stale news events.

    Runs before every collection, so the data in the repository fixes itself
    without a manual migration step. Returns {"files": n, "removed": n}.
    """
    from .sources.gdelt import MAX_AGE_DAYS

    files = removed = 0
    for path in sorted((data_dir / "events").glob("*/*.json")):
        raw = _load(path)
        current = _load_events(path)
        kept = [e for e in current.values() if not _stale_news(e, MAX_AGE_DAYS)]
        removed += len(raw) - len(kept)
        records = sorted(kept, key=lambda e: (e["occurred_at"], e["id"]))
        if records != raw:
            files += 1
            if records:
                path.write_text(_dump_lines(records), encoding="utf-8")
            else:
                path.unlink()
    return {"files": files, "removed": removed}


def merge_events(data_dir: Path, source: str, events: list[dict]) -> dict:
    """Upsert events by id. Returns counts {"new": n, "updated": n}."""
    by_month = defaultdict(list)
    for ev in events:
        by_month[ev["occurred_at"][:7]].append(ev)

    new = updated = 0
    for month, batch in by_month.items():
        path = data_dir / "events" / source / f"{month}.json"
        current = _load_events(path)
        for ev in batch:
            old = current.get(ev["id"])
            if old is None:
                new += 1
            elif old != ev:
                if source == "gdelt":
                    ev = _combine_news(old, ev)
                else:
                    _keep_translations(old, ev)
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
        if res.get("skipped") and prev:
            # Keep the history of a source that is only paused.
            entry = dict(prev, skipped=res["skipped"], finished_at=res["finished_at"])
            status[source] = entry
            continue
        entry = dict(res)
        # Remember the last success even when this run failed.
        entry["last_success"] = res["finished_at"] if res["ok"] else prev.get("last_success")
        status[source] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
