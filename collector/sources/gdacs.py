"""GDACS disaster alerts (UN/EC joint system; free with attribution).

Covers earthquakes, tropical cyclones, floods, volcanoes, droughts, wildfires.
The alert level (Green/Orange/Red) is an estimate of humanitarian impact,
which is exactly what we want for "severity".

GDACS(国連と EU の共同の災害警報システム)。地震・台風・洪水・火山・干ばつ・
山火事を扱う。警報レベル(緑/橙/赤)は「人道的な影響の推定」なので、深刻度に
そのまま使える。地震は USGS と重複するが、GDACS は影響の見積もりを持つので両方
集め、分析(analysis.py)で同じ地震を 1 件として数える。
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

# GDACS の災害種別コード → 共通の type 名。
TYPES = {
    "EQ": "earthquake",
    "TC": "tropical_cyclone",
    "FL": "flood",
    "VO": "volcano",
    "DR": "drought",
    "WF": "wildfire",
    "TS": "tsunami",
}
# 警報レベル → 深刻度。GDACS には黄色がないので medium は使わない。
# orange は「国際支援が必要になりうる」水準なので high とする。
ALERT_TO_LEVEL = {"green": "low", "orange": "high", "red": "critical"}


def _iso(s):
    """GDACS の日時文字列を ISO 8601(UTC)にそろえる。

    GDACS は項目によって日時の書式が違うため、知っている書式を順に試す。
    どれにも合わなければ None(make_event の検証で弾かれ、その件は飛ばされる)。
    """
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
    """GDACS の応答を Event のリストに変換する(通信なし、テスト可能)。"""
    out = []
    for f in doc.get("features", []):
        p = f.get("properties") or {}
        etype = (p.get("eventtype") or "").upper()
        eid = p.get("eventid")
        if not etype or eid is None:
            continue  # 種別か ID がないと出典リンクも作れないので使わない
        geom = f.get("geometry") or {}
        lat = lon = None
        if geom.get("type") == "Point":
            lon, lat = geom["coordinates"][:2]
        urls = p.get("url") or {}
        report = urls.get("report") if isinstance(urls, dict) else None
        # レポート URL がない場合も、種別と ID から公式ページの URL を組み立てる
        # (出典リンクは必須のルールのため)。
        report = report or f"https://www.gdacs.org/report.aspx?eventtype={etype}&eventid={eid}"
        sev = p.get("severitydata") or {}
        alert = (p.get("alertlevel") or "").lower()
        countries = p.get("affectedcountries") or []
        # 複数の国にまたがる災害は、代表の国を country_iso3 に、全ての国を
        # attrs.affected_iso3 に入れる。
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
                confidence="high",  # 国連・EU の公的機関による一次情報
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
    """API から現在の災害一覧を取得して変換する。"""
    return parse(http.get_json(FEED_URL, headers={"Accept": "application/json"}))
