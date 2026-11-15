"""USGS earthquakes (public domain, GeoJSON, near real time).

Feed docs: https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php
We take M4.5+ from the last day; smaller quakes rarely become crises.

USGS(米国地質調査所)の地震情報。パブリックドメインで、ほぼリアルタイム。
M4.5 以上・直近 1 日分だけを取る。それより小さい地震が人道危機になることは
まれで、件数だけが増えて見通しが悪くなるため。毎時取得しているので、1 日分の
窓で取りこぼしなく重なりを持たせられる。
"""

from __future__ import annotations

import sys

from .. import geo, http
from ..schema import from_epoch_ms, make_event

NAME = "usgs"
KIND = "events"
CADENCE = "hourly"
FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"

# USGS PAGER alert = estimated impact on people, better than magnitude alone.
# PAGER は「人への被害の推定」。同じ M7 でも無人地帯と都市直下では被害が全く
# 違うので、マグニチュードより PAGER を優先して深刻度に使う。
PAGER_TO_LEVEL = {"green": "low", "yellow": "medium", "orange": "high", "red": "critical"}


def _level(mag, alert):
    """深刻度を決める。PAGER があればそれを使い、なければマグニチュードで代用する。

    代用の目安: M7 以上 = high、M6 以上 = medium、それ未満 = low。
    critical は PAGER の red(大きな人的被害の推定)のときだけ付ける。
    """
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
    """USGS の GeoJSON を共通形式の Event のリストに変換する(通信なし、テスト可能)。

    1 件の変換に失敗しても全体は止めず、その件だけ飛ばしてログに残す。
    """
    out = []
    for f in doc.get("features", []):
        p = f.get("properties") or {}
        if p.get("type") not in (None, "earthquake"):
            continue  # skip quarry blasts etc.(採石の発破などは地震ではないので除く)
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
                # 地震学者の確認済み(reviewed)なら high、自動判定のままなら medium。
                confidence="high" if p.get("status") == "reviewed" else "medium",
                lat=coords[1],
                lon=coords[0],
                place=place,
                # USGS は国コードを持たないので、地名の末尾(", Chile" など)から求める。
                # 海域の地震は国に属さないので None のまま(推測しない)。
                country_iso3=geo.iso3_from_place(place),
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
    """フィードを取得して変換する(収集時に run.py から呼ばれる)。"""
    return parse(http.get_json(FEED_URL))
