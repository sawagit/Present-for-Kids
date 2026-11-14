"""GDACS disaster alerts (UN/EC joint system; free with attribution).

Covers earthquakes, tropical cyclones, floods, volcanoes, droughts, wildfires.
The alert level (Green/Orange/Red) is an estimate of humanitarian impact,
which is exactly what we want for "severity".
"""

from __future__ import annotations

import sys
from datetime import datetime

from .. import http
from ..schema import iso_utc, make_event

NAME = "gdacs"
KIND = "events"
CADENCE = "hourly"
FEED_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP"

TYPES = {
    "EQ": "earthquake",
    "TC": "tropical_cyclone",
    "FL": "flood",
    "VO": "volcano",
    "DR": "drought",
    "WF": "wildfire",
    "TS": "tsunami",
}
ALERT_TO_LEVEL = {"green": "low", "orange": "high", "red": "critical"}


def _iso(s):
    if not s:
        return None
    s = s.rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%d/%b/%Y %H:%M:%S"):
        try:
            return iso_utc(datetime.strptime(s, fmt))
        except ValueError:
            continue
    return None


def parse(doc: dict) -> list[dict]:
    out = []
    for f in doc.get("features", []):
        p = f.get("properties") or {}
        etype = (p.get("eventtype") or "").upper()
        eid = p.get("eventid")
        if not etype or eid is None:
            continue
        geom = f.get("geometry") or {}
        lat = lon = None
        if geom.get("type") == "Point":
            lon, lat = geom["coordinates"][:2]
        urls = p.get("url") or {}
        report = urls.get("report") if isinstance(urls, dict) else None
        report = report or f"https://www.gdacs.org/report.aspx?eventtype={etype}&eventid={eid}"
        sev = p.get("severitydata") or {}
        alert = (p.get("alertlevel") or "").lower()
        countries = p.get("affectedcountries") or []
        iso3 = p.get("iso3") or (countries[0].get("iso3") if countries else None)
        title = p.get("name") or p.get("description") or f"{TYPES.get(etype, etype)} {eid}"
        try:
            out.append(make_event(
                source=NAME,
                source_id=f"{etype}{eid}",
                category="disaster_climate",
                type_=TYPES.get(etype, etype.lower()),
                occurred_at=_iso(p.get("fromdate")),
                updated_at=_iso(p.get("datemodified")),
                title_en=title,
                urls=[report],
                confidence="high",
                lat=lat,
                lon=lon,
                place=p.get("country"),
                country_iso3=iso3 or None,
                severity_level=ALERT_TO_LEVEL.get(alert),
                severity_value=sev.get("severity"),
                severity_unit=sev.get("severityunit"),
                attrs={
                    "alert_level": p.get("alertlevel"),
                    "alert_score": p.get("alertscore"),
                    "episode_id": p.get("episodeid"),
                    "severity_text": sev.get("severitytext"),
                    "to_date": _iso(p.get("todate")),
                    "is_current": p.get("iscurrent"),
                    "affected_iso3": sorted({c.get("iso3") for c in countries if c.get("iso3")}),
                },
            ))
        except (ValueError, KeyError, TypeError) as e:
            print(f"[{NAME}] skip: {e}", file=sys.stderr)
    return out


def fetch(ctx) -> list[dict]:
    return parse(http.get_json(FEED_URL, headers={"Accept": "application/json"}))
