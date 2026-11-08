"""ReliefWeb reports (UN OCHA; humanitarian situation reports, news, appeals).

API v2: https://apidoc.reliefweb.int/
ReliefWeb asks every client to send an `appname`, and new apps must be
approved by ReliefWeb (request form linked from the API docs). Set the
approved name in the RELIEFWEB_APPNAME secret. Without it we still try with
our default name so the verification job shows what the API answers.

We store title, metadata and link only, never the report body (copyright:
each publishing organisation sets its own terms).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from .. import http
from ..schema import iso_utc, make_event

NAME = "reliefweb"
KIND = "events"
CADENCE = "daily"
API_URL = "https://api.reliefweb.int/v2/reports?appname={appname}"
DEFAULT_APPNAME = "present-for-kids"
PAGE = 500  # API maximum per call; enough for one day

SKIP_FORMATS = {"Map", "Infographic", "Manual and Guideline", "Interactive"}

# First matching rule wins. Disaster types are checked before themes.
DISASTER_RULES = [({"Epidemic", "Insect Infestation"}, "public_health")]
THEME_RULES = [
    ({"Health", "HIV/Aids"}, "public_health"),
    ({"Protection and Human Rights", "Gender"}, "human_rights"),
    ({"Safety and Security", "Peacekeeping and Peacebuilding", "Mine Action"}, "conflict_security"),
    ({"Food and Nutrition", "Agriculture", "Humanitarian Financing"}, "economy_inequality"),
    ({"Climate Change and Environment", "Disaster Management"}, "disaster_climate"),
    ({"Education"}, "culture_society"),
]
DISPLACEMENT_WORDS = ("refugee", "displace", "idp", "migrant", "asylum", "returnee")


def _names(items):
    return {i.get("name") for i in (items or []) if i.get("name")}


def categorize(themes: set, disaster_types: set) -> str:
    for keys, cat in DISASTER_RULES:
        if keys & disaster_types:
            return cat
    if disaster_types:
        return "disaster_climate"
    for keys, cat in THEME_RULES:
        if keys & themes:
            return cat
    return "human_rights"  # humanitarian situation, refined by the AI stage


def parse(doc: dict) -> list[dict]:
    out = []
    for item in doc.get("data", []):
        f = item.get("fields") or {}
        formats = _names(f.get("format"))
        if formats and formats <= SKIP_FORMATS:
            continue
        themes = _names(f.get("theme"))
        dtypes = _names(f.get("disaster_type"))
        title = f.get("title") or ""
        pc = f.get("primary_country") or {}
        loc = pc.get("location") or {}
        tags = []
        if "Shelter and Non-Food Items" in themes or any(w in title.lower() for w in DISPLACEMENT_WORDS):
            tags.append("displacement")
        direction = "improving" if "Recovery and Reconstruction" in themes else "neutral"
        date = (f.get("date") or {})
        try:
            out.append(make_event(
                source=NAME,
                source_id=item.get("id") or f.get("id"),
                category=categorize(themes, dtypes),
                type_="report:" + (sorted(formats)[0] if formats else "unknown"),
                occurred_at=_norm(date.get("original") or date.get("created")),
                updated_at=_norm(date.get("changed")),
                title_en=title,
                urls=[f.get("url_alias") or f.get("url")],
                confidence="medium",
                lat=loc.get("lat"),
                lon=loc.get("lon"),
                place=pc.get("name"),
                country_iso3=(pc.get("iso3") or "").upper() or None,
                direction=direction,
                tags=tags,
                attrs={
                    "publishers": sorted(s.get("shortname") or s.get("name") for s in f.get("source") or []),
                    "countries_iso3": sorted({(c.get("iso3") or "").upper() for c in f.get("country") or [] if c.get("iso3")}),
                    "themes": sorted(themes),
                    "disaster_types": sorted(dtypes),
                    "disasters": sorted(_names(f.get("disaster"))),
                    "language": sorted(l.get("code") for l in f.get("language") or [] if l.get("code")),
                },
            ))
        except ValueError as e:
            print(f"[reliefweb] skip: {e}", file=sys.stderr)
    return out


def _norm(s):
    if not s:
        return None
    # "2026-11-07T10:20:30+00:00"
    return iso_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))


def query(since: datetime) -> dict:
    return {
        "limit": PAGE,
        "sort": ["date.created:desc"],
        "filter": {"field": "date.created", "value": {"from": iso_utc(since).replace("Z", "+00:00")}},
        "fields": {"include": [
            "title", "url", "url_alias", "date", "primary_country", "country",
            "source", "theme", "disaster_type", "disaster", "format", "language",
        ]},
    }


def fetch(ctx) -> list[dict]:
    appname = os.environ.get("RELIEFWEB_APPNAME") or DEFAULT_APPNAME
    since = datetime.now(timezone.utc) - timedelta(hours=ctx.lookback_hours)
    try:
        doc = http.post_json(API_URL.format(appname=appname), query(since))
    except http.FetchError as e:
        hint = "" if os.environ.get("RELIEFWEB_APPNAME") else (
            " (RELIEFWEB_APPNAME is not set: ReliefWeb may require an approved appname)")
        raise http.FetchError(f"{e}{hint}") from None
    return parse(doc)
