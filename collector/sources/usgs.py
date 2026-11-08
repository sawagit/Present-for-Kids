"""USGS earthquakes (public domain, GeoJSON, near real time).

Feed docs: https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php
We take M4.5+ from the last day; smaller quakes rarely become crises.
"""

from __future__ import annotations

import sys

from .. import http
from ..schema import from_epoch_ms, make_event

NAME = "usgs"
KIND = "events"
CADENCE = "hourly"
FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"

# USGS PAGER alert = estimated impact on people, better than magnitude alone.
PAGER_TO_LEVEL = {"green": "low", "yellow": "medium", "orange": "high", "red": "critical"}


def _level(mag, alert):
    if alert in PAGER_TO_LEVEL:
        return PAGER_TO_LEVEL[alert]
    if mag is None:
        return None
    if mag >= 7.0:
        return "high"
    if mag >= 6.0:
        return "medium"
    return "low"


def parse(doc: dict) -> list[dict]:
    out = []
    for f in doc.get("features", []):
        p = f.get("properties") or {}
        if p.get("type") not in (None, "earthquake"):
            continue  # skip quarry blasts etc.
        coords = (f.get("geometry") or {}).get("coordinates") or [None, None, None]
        mag = p.get("mag")
        alert = p.get("alert")
        place = p.get("place")
        title = p.get("title") or f"M {mag} - {place}"
        try:
            out.append(make_event(
                source=NAME,
                source_id=f["id"],
                category="disaster_climate",
                type_="earthquake",
                occurred_at=from_epoch_ms(p["time"]),
                updated_at=from_epoch_ms(p["updated"]) if p.get("updated") else None,
                title_en=title,
                urls=[p.get("url")],
                confidence="high" if p.get("status") == "reviewed" else "medium",
                lat=coords[1],
                lon=coords[0],
                place=place,
                severity_level=_level(mag, alert),
                severity_value=mag,
                severity_unit="magnitude",
                attrs={
                    "depth_km": coords[2] if len(coords) > 2 else None,
                    "pager_alert": alert,
                    "tsunami": bool(p.get("tsunami")),
                    "felt": p.get("felt"),
                    "significance": p.get("sig"),
                    "status": p.get("status"),
                },
            ))
        except (ValueError, KeyError, TypeError) as e:
            print(f"[{NAME}] skip: {e}", file=sys.stderr)
    return out


def fetch(ctx) -> list[dict]:
    return parse(http.get_json(FEED_URL))
