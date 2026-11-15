"""Normalized record shapes shared by every source.

Two kinds of records come out of the collector:

* Event      something that happened at a place and time (an earthquake, a
             protest, a humanitarian report). Stored under data/events/.
* Indicator  a measured value for an area and period (refugees from a
             country in a year). Stored under data/indicators/.

Links (cause/effect between events) are produced later by the AI stage and
are not part of Phase 0.

Titles and summaries are bilingual from the start: {"en": ..., "ja": ...}.
Sources give us English (or the original language); the Japanese side is
filled by the AI stage, so it is None here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import geo

# 2 (2026-11-15): location.region / location.subregion added (UN M49 names),
#   country_iso3 filled for GDELT and USGS, stable GDELT ids. Only additions:
#   readers of version 1 keep working. Older files are upgraded in place by
#   upgrade_event() the next time the collector runs.
SCHEMA_VERSION = 2

# Layer 1 categories, all treated equally (decision 2026-11-01).
CATEGORIES = (
    "conflict_security",      # 武力紛争・安全保障
    "disaster_climate",       # 自然災害・気候
    "human_rights",           # 人権・弾圧
    "economy_inequality",     # 経済・格差・食料
    "public_health",          # 公衆衛生
    "culture_society",        # 文化・社会分断
    "ai_technology",          # AI・技術・サイバー
    "politics_governance",    # 政治・ガバナンス
)

# Cross-cutting tags (layer 0). "displacement" = 難民・移動.
TAGS = ("displacement",)

# Improvements are first-class events, not an afterthought (site vision).
DIRECTIONS = ("worsening", "improving", "turning_point", "neutral")

SEVERITY_LEVELS = ("low", "medium", "high", "critical")

# How much we trust that the event itself happened as described.
# high = authoritative primary source (USGS, GDACS, UN agencies)
# medium = curated secondary source (ReliefWeb reports)
# low = machine-coded from news (GDELT)
CONFIDENCE = ("high", "medium", "low")

# Coordinates are rounded so we never point at a house or a person
# (privacy policy in the planning doc: city level at most). 2 decimals ≈ 1 km.
COORD_DECIMALS = 2


def iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_epoch_ms(ms: int | float) -> str:
    return iso_utc(datetime.fromtimestamp(ms / 1000, tz=timezone.utc))


def round_coord(v):
    if v is None or v == "":
        return None
    try:
        return round(float(v), COORD_DECIMALS)
    except (TypeError, ValueError):
        return None


def make_event(
    *,
    source: str,
    source_id: str,
    category: str,
    occurred_at: str,
    title_en: str,
    urls: list[str],
    confidence: str,
    type_: str,
    lat=None,
    lon=None,
    place: str | None = None,
    country_iso3: str | None = None,
    severity_level: str | None = None,
    severity_value=None,
    severity_unit: str | None = None,
    direction: str = "worsening",
    tags: list[str] | None = None,
    updated_at: str | None = None,
    attrs: dict | None = None,
) -> dict:
    region, subregion = geo.regions(country_iso3)
    ev = {
        "id": f"{source}:{source_id}",
        "schema": SCHEMA_VERSION,
        "source": source,
        "source_id": str(source_id),
        "category": category,
        "type": type_,
        "tags": sorted(set(tags or [])),
        "direction": direction,
        "occurred_at": occurred_at,
        "updated_at": updated_at,
        "title": {"en": title_en, "ja": None},
        "summary": {"en": None, "ja": None},
        "location": {
            "lat": round_coord(lat),
            "lon": round_coord(lon),
            "place": place,
            "country_iso3": country_iso3,
            "region": region,          # M49 region, e.g. "Africa"
            "subregion": subregion,    # M49 subregion, e.g. "Eastern Africa"
        },
        "severity": {
            "level": severity_level,
            "value": severity_value,
            "unit": severity_unit,
        },
        "confidence": confidence,
        "urls": [u for u in urls if u],
        "attrs": attrs or {},
    }
    validate_event(ev)
    return ev


def upgrade_event(ev: dict) -> dict:
    """Bring a stored event up to SCHEMA_VERSION (idempotent, never drops data)."""
    if ev.get("schema", 1) >= SCHEMA_VERSION:
        return ev
    loc = ev["location"]
    iso3 = loc.get("country_iso3")
    if not iso3 and ev["source"] == "gdelt":
        iso3 = geo.iso3_from_fips(ev["attrs"].get("country_fips"))
    if not iso3 and ev["source"] == "usgs":
        iso3 = geo.iso3_from_place(loc.get("place"))
    loc["country_iso3"] = iso3
    loc["region"], loc["subregion"] = geo.regions(iso3)
    if ev["source"] == "gdelt":
        from .sources import gdelt  # local import: sources import this module
        sid = gdelt.stable_id(ev["attrs"].get("cameo_code", "")[:3], loc.get("place") or "",
                              ev["occurred_at"][:10].replace("-", ""))
        ev["id"], ev["source_id"] = f"gdelt:{sid}", sid
    ev["schema"] = SCHEMA_VERSION
    return ev


def validate_event(ev: dict) -> None:
    """Raise ValueError if an event breaks the contract the site relies on."""
    problems = []
    if ev["category"] not in CATEGORIES:
        problems.append(f"category={ev['category']!r}")
    if ev["direction"] not in DIRECTIONS:
        problems.append(f"direction={ev['direction']!r}")
    if ev["confidence"] not in CONFIDENCE:
        problems.append(f"confidence={ev['confidence']!r}")
    lvl = ev["severity"]["level"]
    if lvl is not None and lvl not in SEVERITY_LEVELS:
        problems.append(f"severity.level={lvl!r}")
    for t in ev["tags"]:
        if t not in TAGS:
            problems.append(f"tag={t!r}")
    if not ev["occurred_at"] or not ev["occurred_at"].endswith("Z"):
        problems.append(f"occurred_at={ev['occurred_at']!r}")
    if not ev["urls"]:
        # Every event must point back to its source (no claim without evidence).
        problems.append("urls empty")
    if not ev["title"]["en"]:
        problems.append("title.en empty")
    if problems:
        raise ValueError(f"invalid event {ev.get('id')}: " + ", ".join(problems))


def make_indicator(
    *,
    source: str,
    indicator: str,
    area_iso3: str | None,
    area_name: str | None,
    period: str,
    value,
    unit: str,
    url: str,
    attrs: dict | None = None,
) -> dict:
    return {
        "id": f"{source}:{indicator}:{area_iso3 or area_name}:{period}",
        "schema": SCHEMA_VERSION,
        "source": source,
        "indicator": indicator,
        "area": {"iso3": area_iso3, "name": area_name},
        "period": period,
        "value": value,
        "unit": unit,
        "url": url,
        "attrs": attrs or {},
    }
