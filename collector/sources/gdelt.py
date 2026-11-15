"""GDELT 2.0 Events (machine-coded from world news, every 15 minutes; free).

GDELT is huge and noisy (its own dashboard is a "prototype" with many false
positives), so we keep only a small, filtered slice:

* root events only (IsRootEvent = 1), with a resolvable action location
* CAMEO root codes that matter for crises, and also the cooperative ones
  (aid, ceasefire, easing) so improvements are collected with the same weight
* at least MIN_ARTICLES articles, duplicates merged, capped to the top N

The category mapping below is deliberately coarse. The AI stage refines it.
No article text is stored: only the source URL and GDELT's coded metadata.

Format: http://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf
"""

from __future__ import annotations

import csv
import io
import os
import sys
import zipfile
from datetime import datetime, timedelta, timezone

from .. import http
from ..schema import iso_utc, make_event

NAME = "gdelt"
KIND = "events"
CADENCE = "daily"
LASTUPDATE_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
SLICE_URL = "http://data.gdeltproject.org/gdeltv2/{ts}.export.CSV.zip"

# Column positions in the 61-column export file.
C = dict(
    id=0, sqldate=1, actor1=6, actor1_cc=7, actor2=16, actor2_cc=17,
    is_root=25, code=26, base_code=27, root_code=28, quad=29, goldstein=30,
    mentions=31, sources=32, articles=33, tone=34,
    geo_type=51, geo_name=52, geo_cc=53, geo_lat=56, geo_lon=57, geo_fid=58,
    date_added=59, url=60,
)
NCOLS = 61

# root code -> (category, direction, label)
ROOTS = {
    "06": ("politics_governance", "improving", "Material cooperation"),
    "07": ("economy_inequality", "improving", "Aid provided"),
    "08": ("politics_governance", "improving", "Concession / easing"),
    "14": ("culture_society", "worsening", "Protest"),
    "17": ("human_rights", "worsening", "Coercion / repression"),
    "18": ("conflict_security", "worsening", "Assault"),
    "19": ("conflict_security", "worsening", "Armed clash"),
    "20": ("conflict_security", "worsening", "Mass violence"),
}
# Finer overrides by base code.
BASES = {
    "087": ("conflict_security", "turning_point", "Military de-escalation / ceasefire"),
    "073": ("economy_inequality", "improving", "Humanitarian aid"),
    "075": ("human_rights", "improving", "Protection granted"),
    "083": ("politics_governance", "improving", "Political reform accepted"),
}

MIN_ARTICLES = int(os.environ.get("GDELT_MIN_ARTICLES", "5"))
MAX_EVENTS = int(os.environ.get("GDELT_MAX_EVENTS", "300"))
# News often mentions old events ("a year ago..."); GDELT then codes them with
# the old date. Keep only events dated within this many days of being seen.
MAX_AGE_DAYS = int(os.environ.get("GDELT_MAX_AGE_DAYS", "7"))


def _too_old(r) -> bool:
    try:
        event = datetime.strptime(r[C["sqldate"]], "%Y%m%d")
        seen = datetime.strptime(r[C["date_added"]][:8], "%Y%m%d")
    except ValueError:
        return True
    return (seen - event).days > MAX_AGE_DAYS


def _level(root, articles):
    if root == "20" or articles >= 100:
        return "high"
    if articles >= 25:
        return "medium"
    return "low"


def _keep(r) -> bool:
    """Cheap row filter, applied while streaming so memory stays small."""
    if len(r) < NCOLS or r[C["is_root"]] != "1":
        return False
    if r[C["base_code"]] not in BASES and r[C["root_code"]] not in ROOTS:
        return False
    if not r[C["geo_lat"]] or not r[C["url"]] or _too_old(r):
        return False
    try:
        return int(r[C["articles"]]) >= MIN_ARTICLES
    except ValueError:
        return False


def parse_rows(rows) -> list[dict]:
    """Filter and merge raw export rows into events (pure function, testable)."""
    groups: dict[tuple, dict] = {}
    for r in rows:
        if not _keep(r):
            continue
        spec = BASES.get(r[C["base_code"]]) or ROOTS[r[C["root_code"]]]
        articles = int(r[C["articles"]])
        # Same kind of event at the same place on the same day = one event.
        key = (r[C["base_code"]], r[C["geo_fid"]] or r[C["geo_name"]], r[C["sqldate"]])
        g = groups.get(key)
        if g is None:
            groups[key] = {"row": r, "spec": spec, "articles": articles,
                           "urls": [r[C["url"]]], "ids": [r[C["id"]]]}
        else:
            g["articles"] += articles
            g["ids"].append(r[C["id"]])
            if r[C["url"]] not in g["urls"]:
                g["urls"].append(r[C["url"]])
            if articles > int(g["row"][C["articles"]]):
                g["row"] = r

    ranked = sorted(groups.values(), key=lambda g: g["articles"], reverse=True)[:MAX_EVENTS]
    out = []
    for g in ranked:
        r, (category, direction, label) = g["row"], g["spec"]
        actors = " / ".join(a for a in (r[C["actor1"]], r[C["actor2"]]) if a)
        place = r[C["geo_name"]]
        title = f"{label}: {actors} ({place})" if actors else f"{label} ({place})"
        d = r[C["sqldate"]]
        try:
            out.append(make_event(
                source=NAME,
                source_id=r[C["id"]],
                category=category,
                type_=f"cameo_{r[C['code']]}",
                occurred_at=f"{d[:4]}-{d[4:6]}-{d[6:8]}T00:00:00Z",
                updated_at=_ts(r[C["date_added"]]),
                title_en=title,
                urls=g["urls"][:5],
                confidence="low",  # machine-coded from news; never shown as fact
                lat=r[C["geo_lat"]],
                lon=r[C["geo_lon"]],
                place=place,
                severity_level=_level(r[C["root_code"]], g["articles"]),
                severity_value=g["articles"],
                severity_unit="articles",
                direction=direction,
                attrs={
                    "cameo_code": r[C["code"]],
                    "quad_class": int(r[C["quad"]] or 0),
                    "goldstein": float(r[C["goldstein"]] or 0),
                    "avg_tone": round(float(r[C["tone"]] or 0), 2),
                    "num_mentions": int(r[C["mentions"]] or 0),
                    "country_fips": r[C["geo_cc"]] or None,
                    "actor1_country": r[C["actor1_cc"]] or None,
                    "actor2_country": r[C["actor2_cc"]] or None,
                    "merged_event_ids": g["ids"][:20],
                },
            ))
        except ValueError as e:
            print(f"[gdelt] skip: {e}", file=sys.stderr)
    return out


def _ts(s):
    try:
        return iso_utc(datetime.strptime(s, "%Y%m%d%H%M%S"))
    except (TypeError, ValueError):
        return None


def _rows_from_zip(raw: bytes):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            with z.open(name) as fh:
                text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="")
                yield from csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)


def latest_slice(lastupdate_text: str) -> datetime:
    for line in lastupdate_text.splitlines():
        parts = line.split()
        if parts and parts[-1].endswith(".export.CSV.zip"):
            ts = parts[-1].rsplit("/", 1)[-1].split(".")[0]
            return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    raise ValueError("no export file in lastupdate.txt")


def fetch(ctx) -> list[dict]:
    """Download the latest `ctx.gdelt_slices` 15-minute files (96 = one day)."""
    newest = latest_slice(http.get(LASTUPDATE_URL).decode("utf-8"))
    rows, ok, missing = [], 0, 0
    for i in range(ctx.gdelt_slices):
        ts = (newest - timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S")
        try:
            raw = http.get(SLICE_URL.format(ts=ts), retries=2)
        except http.FetchError as e:
            missing += 1  # GDELT occasionally skips a slice
            print(f"[gdelt] missing slice {ts}: {e}", file=sys.stderr)
            continue
        rows.extend(r for r in _rows_from_zip(raw) if _keep(r))
        ok += 1
    if ok == 0:
        raise http.FetchError("no GDELT slice could be downloaded")
    print(f"[gdelt] slices ok={ok} missing={missing} kept_rows={len(rows)}", file=sys.stderr)
    return parse_rows(rows)
