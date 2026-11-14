"""UNHCR Refugee Data Finder: forcibly displaced people by country of origin.

API: https://api.unhcr.org/docs/refugee-statistics.html (free, attribution).
This is annual statistics, so it produces Indicators, not Events. It runs
weekly; the values only change when UNHCR publishes a new year.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from .. import http
from ..schema import make_indicator

NAME = "unhcr"
KIND = "indicators"
CADENCE = "weekly"
API_URL = ("https://api.unhcr.org/population/v1/population/"
           "?limit=1000&page={page}&yearFrom={year}&yearTo={year}&coo_all=true")
PUBLIC_URL = "https://www.unhcr.org/refugee-statistics/"

# API field -> our indicator name
FIELDS = {
    "refugees": "refugees",
    "asylum_seekers": "asylum_seekers",
    "idps": "internally_displaced",
    "returned_refugees": "returned_refugees",
    "returned_idps": "returned_idps",
    "stateless": "stateless",
    "oip": "other_in_need_of_protection",
}


def _num(v):
    if v in (None, "", "-"):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse(items: list[dict], year: int) -> list[dict]:
    out = []
    for it in items:
        iso3 = it.get("coo_iso") or it.get("coo")
        if not iso3 or iso3 == "-":
            continue
        name = it.get("coo_name")
        for field, indicator in FIELDS.items():
            v = _num(it.get(field))
            if v is None:
                continue
            out.append(make_indicator(
                source=NAME,
                indicator=f"{indicator}_by_origin",
                area_iso3=iso3,
                area_name=name,
                period=str(year),
                value=v,
                unit="people",
                url=PUBLIC_URL,
            ))
    return out


def _fetch_year(year: int) -> list[dict]:
    items, page, max_pages = [], 1, 1
    while page <= max_pages:
        doc = http.get_json(API_URL.format(page=page, year=year))
        items.extend(doc.get("items") or [])
        max_pages = int(doc.get("maxPages") or 1)
        page += 1
    return items


def fetch(ctx) -> list[dict]:
    this_year = datetime.now(timezone.utc).year
    for year in range(this_year, this_year - 4, -1):
        items = _fetch_year(year)
        if items:
            print(f"[unhcr] latest year with data: {year} ({len(items)} origins)", file=sys.stderr)
            return parse(items, year)
    raise http.FetchError("UNHCR returned no data for the last 4 years")
