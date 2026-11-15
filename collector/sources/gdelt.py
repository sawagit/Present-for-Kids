"""GDELT 2.0 Events (machine-coded from world news, every 15 minutes; free).

GDELT is huge and noisy (its own dashboard is a "prototype" with many false
positives), so we keep only a small, filtered slice:

* root events only (IsRootEvent = 1), with a resolvable action location
* CAMEO root codes that matter for crises, and also the cooperative ones
  (aid, ceasefire, easing) so improvements are collected with the same weight
* at least MIN_ARTICLES articles, duplicates merged, capped to the top N
* only events dated within MAX_AGE_DAYS of when the news reported them
  (articles often recall old events, which GDELT then codes with the old date)
* at most MAX_PER_COUNTRY events per country, so English-language news about
  one country (mostly the US) cannot crowd out the rest of the world

The category mapping below is deliberately coarse. The AI stage refines it.
No article text is stored: only the source URL and GDELT's coded metadata.

Format: http://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import sys
import zipfile
from datetime import datetime, timedelta, timezone

from .. import geo, http
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
# We collect daily, so 3 days leaves room for late reporting and time zones.
MAX_AGE_DAYS = int(os.environ.get("GDELT_MAX_AGE_DAYS", "3"))
MAX_PER_COUNTRY = int(os.environ.get("GDELT_MAX_PER_COUNTRY", "30"))

# Why rows were dropped in the last fetch (reported in data/status.json).
LAST_STATS: dict = {}


def _too_old(r) -> bool:
    try:
        event = datetime.strptime(r[C["sqldate"]], "%Y%m%d")
        seen = datetime.strptime(r[C["date_added"]][:8], "%Y%m%d")
    except ValueError:
        return True
    return (seen - event).days > MAX_AGE_DAYS


def stable_id(base_code: str, place: str, sqldate: str) -> str:
    """Same kind of event, same place, same day -> same id on every run.

    GDELT gives every news mention its own row id, so the "first" row of a
    merged group differs from run to run; keying on what the event is keeps
    the daily re-fetch from creating duplicates.
    """
    h = hashlib.sha1(place.encode("utf-8")).hexdigest()[:8]
    return f"{sqldate}-{base_code}-{h}"


def _level(root, articles):
    if root == "20" or articles >= 100:
        return "high"
    if articles >= 25:
        return "medium"
    return "low"


def _reject(r) -> str | None:
    """Why a row is dropped (None = keep). Cheap, applied while streaming."""
    if len(r) < NCOLS or r[C["is_root"]] != "1":
        return "not_root"
    if r[C["base_code"]] not in BASES and r[C["root_code"]] not in ROOTS:
        return "other_code"
    if not r[C["geo_lat"]] or not r[C["url"]]:
        return "no_location"
    try:
        if int(r[C["articles"]]) < MIN_ARTICLES:
            return "few_articles"
    except ValueError:
        return "few_articles"
    if _too_old(r):
        return "old_event"
    return None


def _keep(r) -> bool:
    return _reject(r) is None


def parse_rows(rows) -> list[dict]:
    """Filter and merge raw export rows into events (pure function, testable)."""
    groups: dict[tuple, dict] = {}
    for r in rows:
        if not _keep(r):
            continue
        spec = BASES.get(r[C["base_code"]]) or ROOTS[r[C["root_code"]]]
        articles = int(r[C["articles"]])
        # Same kind of event at the same place on the same day = one event.
        key = (r[C["base_code"]], r[C["geo_name"]], r[C["sqldate"]])
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

    ranked, per_country = [], {}
    for g in sorted(groups.values(), key=lambda g: g["articles"], reverse=True):
        cc = g["row"][C["geo_cc"]]
        if per_country.get(cc, 0) >= MAX_PER_COUNTRY:
            continue
        per_country[cc] = per_country.get(cc, 0) + 1
        ranked.append(g)
        if len(ranked) >= MAX_EVENTS:
            break
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
                source_id=stable_id(r[C["base_code"]], place, d),
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
                country_iso3=geo.iso3_from_fips(r[C["geo_cc"]]),
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
    dropped: dict[str, int] = {}
    for i in range(ctx.gdelt_slices):
        ts = (newest - timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S")
        try:
            raw = http.get(SLICE_URL.format(ts=ts), retries=2)
        except http.FetchError as e:
            missing += 1  # GDELT occasionally skips a slice
            print(f"[gdelt] missing slice {ts}: {e}", file=sys.stderr)
            continue
        for r in _rows_from_zip(raw):
            why = _reject(r)
            if why is None:
                rows.append(r)
            else:
                dropped[why] = dropped.get(why, 0) + 1
        ok += 1
    if ok == 0:
        raise http.FetchError("no GDELT slice could be downloaded")
    print(f"[gdelt] slices ok={ok} missing={missing} kept_rows={len(rows)} dropped={dropped}",
          file=sys.stderr)
    LAST_STATS.clear()
    LAST_STATS.update(slices_ok=ok, slices_missing=missing, kept_rows=len(rows), dropped_rows=dropped)
    return parse_rows(rows)
