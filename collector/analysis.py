"""Groundwork for analysing how crises interact (no AI here, only counting).

Reads every stored event and writes two files under data/analysis/:

* country_weeks.json
    For each country and ISO week: how many events of each category, how
    many worsening / improving, and the event ids. This is the common grid on
    which a disaster, a conflict, a price shock and a refugee flow in the same
    place and time can be put side by side (map shading, timelines).

* link_candidates.json
    Country-weeks where two or more different categories happened together.
    These are only *candidates*: being in the same place and week is not a
    cause. The AI stage reads them, proposes cause-effect links with evidence,
    and a person approves them before anything is published (decision
    2026-11-01). Ordered so the strongest overlaps come first.

The same earthquake is reported by both USGS and GDACS; such pairs are
counted once (USGS kept, as the primary source) and listed in
country_weeks.json under "same_event".
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import geo
from .schema import CATEGORIES, SEVERITY_LEVELS, iso_utc

WEEKS = 12            # how far back the grid goes
MAX_IDS = 10          # event ids kept per category in a candidate
SAME_QUAKE_MINUTES = 15
SAME_QUAKE_KM = 100
# Prefer authoritative sources when two describe the same event.
SOURCE_RANK = {"usgs": 0, "gdacs": 1, "reliefweb": 2, "gdelt": 3}
SEV_RANK = {lvl: i for i, lvl in enumerate(SEVERITY_LEVELS)}


def iso_week(ts: str) -> str:
    y, w, _ = datetime.fromisoformat(ts[:10]).isocalendar()
    return f"{y}-W{w:02d}"


def _km(a: dict, b: dict) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _ts(ev: dict) -> datetime:
    return datetime.fromisoformat(ev["occurred_at"].replace("Z", "+00:00"))


def same_quakes(events: list[dict]) -> dict[str, str]:
    """Map duplicate earthquake id -> the id we keep (USGS over GDACS)."""
    quakes = [e for e in events if e["type"] == "earthquake" and e["location"]["lat"] is not None]
    quakes.sort(key=_ts)
    dup: dict[str, str] = {}
    for i, a in enumerate(quakes):
        for b in quakes[i + 1:]:
            if _ts(b) - _ts(a) > timedelta(minutes=SAME_QUAKE_MINUTES):
                break
            if a["source"] == b["source"] or a["id"] in dup or b["id"] in dup:
                continue
            if _km(a["location"], b["location"]) <= SAME_QUAKE_KM:
                keep, drop = sorted((a, b), key=lambda e: SOURCE_RANK.get(e["source"], 9))
                dup[drop["id"]] = keep["id"]
    return dup


def load_events(data_dir: Path, since: str) -> list[dict]:
    out = []
    for path in sorted((data_dir / "events").glob("*/*.json")):
        if path.stem < since[:7]:
            continue
        out += [e for e in json.loads(path.read_text(encoding="utf-8")) if e["occurred_at"] >= since]
    return out


def _sev(ev: dict) -> int:
    return SEV_RANK.get(ev["severity"]["level"], -1)


def build(data_dir: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(weeks=WEEKS)
    since = iso_utc(start - timedelta(days=start.weekday()))[:10]  # Monday
    events = load_events(data_dir, since)
    dup = same_quakes(events)

    grid: dict[tuple, dict] = {}
    for ev in events:
        iso3 = ev["location"]["country_iso3"]
        if not iso3 or ev["id"] in dup:
            continue  # open ocean, unknown place, or counted under another source
        key = (iso3, iso_week(ev["occurred_at"]))
        cell = grid.get(key)
        if cell is None:
            region, subregion = geo.regions(iso3)
            cell = grid[key] = {"country_iso3": iso3, "week": key[1], "region": region,
                                "subregion": subregion, "total": 0, "categories": {},
                                "_events": defaultdict(list)}
        c = cell["categories"].setdefault(ev["category"], {
            "events": 0, "worsening": 0, "improving": 0, "turning_point": 0,
            "max_severity": None, "sources": []})
        c["events"] += 1
        if ev["direction"] in ("worsening", "improving", "turning_point"):
            c[ev["direction"]] += 1
        if _sev(ev) > SEV_RANK.get(c["max_severity"], -1):
            c["max_severity"] = ev["severity"]["level"]
        if ev["source"] not in c["sources"]:
            c["sources"].append(ev["source"])
        cell["total"] += 1
        cell["_events"][ev["category"]].append(ev)

    cells, candidates = [], []
    for key in sorted(grid):
        cell = grid[key]
        evs = cell.pop("_events")
        for cat, c in cell["categories"].items():
            c["sources"].sort()
            ranked = sorted(evs[cat], key=lambda e: (-_sev(e), -(e["severity"]["value"] or 0), e["id"]))
            c["event_ids"] = [e["id"] for e in ranked[:MAX_IDS]]
        cell["categories"] = {k: cell["categories"][k] for k in CATEGORIES if k in cell["categories"]}
        cells.append(cell)
        if len(cell["categories"]) >= 2:
            # Weight: categories together, then evidence that is not only news-coded.
            strong = sum(1 for c in cell["categories"].values() if c["sources"] != ["gdelt"])
            candidates.append({
                "id": f"{key[0]}:{key[1]}",
                "country_iso3": key[0],
                "week": key[1],
                "region": cell["region"],
                "subregion": cell["subregion"],
                "categories": list(cell["categories"]),
                "non_news_categories": strong,
                "events": {cat: c["event_ids"] for cat, c in cell["categories"].items()},
                "status": "candidate",  # -> proposed (AI) -> approved / rejected (person)
            })
    # Overlaps backed by non-news sources first, then more categories, then newest.
    candidates.sort(key=lambda c: c["country_iso3"])
    candidates.sort(key=lambda c: c["week"], reverse=True)
    candidates.sort(key=lambda c: (c["non_news_categories"], len(c["categories"])), reverse=True)

    meta = {"generated_at": iso_utc(now), "since": since, "weeks": WEEKS}
    out_dir = data_dir / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    _write(out_dir / "country_weeks.json", dict(meta, same_event=dup, cells=cells))
    _write(out_dir / "link_candidates.json", dict(meta, candidates=candidates))
    return {"cells": len(cells), "candidates": len(candidates), "same_event": len(dup)}


def _write(path: Path, doc: dict) -> None:
    """One cell / candidate per line, like the event files (readable Git diffs)."""
    items_key = "cells" if "cells" in doc else "candidates"
    items = doc[items_key]
    head = {k: v for k, v in doc.items() if k != items_key}
    lines = [json.dumps(i, ensure_ascii=False, sort_keys=True) for i in items]
    body = "[\n" + ",\n".join(lines) + "\n]" if lines else "[]"
    text = json.dumps(head, ensure_ascii=False, sort_keys=True, indent=1)[:-2]
    path.write_text(text + f',\n "{items_key}": {body}\n}}\n', encoding="utf-8")
