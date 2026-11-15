"""ReliefWeb reports (UN OCHA; humanitarian situation reports, news, appeals).

API v2: https://apidoc.reliefweb.int/
ReliefWeb only answers requests with an approved `appname` (request form:
https://apidoc.reliefweb.int/parameters#appname). Put the approved name in
the RELIEFWEB_APPNAME secret. Until then this source is skipped, not failed.

We store title, metadata and link only, never the report body (copyright:
each publishing organisation sets its own terms).

ReliefWeb(国連 OCHA)の人道レポート。API は承認済みの appname が必須なので、
Secret の RELIEFWEB_APPNAME が未登録の間は「失敗」ではなく「スキップ」にする。
レポート本文は発行団体ごとに著作権の条件が違うため保存せず、タイトル・
メタデータ・リンクだけを持つ。
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
PAGE = 500  # API maximum per call; enough for one day
# 1 回で取れる上限。ReliefWeb の 1 日の新着はこれより少ないので 1 ページで足りる。

# 地図・インフォグラフィックなどは「出来事の報告」ではないので除く。
SKIP_FORMATS = {"Map", "Infographic", "Manual and Guideline", "Interactive"}

# First matching rule wins. Disaster types are checked before themes.
# 分野の判定ルール。上から順に見て最初に当たったものを使う。災害種別(疫病など)は
# テーマより具体的なので先に見る。ここでの分類は大まかで、AI 段階で精緻にする。
DISASTER_RULES = [({"Epidemic", "Insect Infestation"}, "public_health")]
THEME_RULES = [
    ({"Health", "HIV/Aids"}, "public_health"),
    ({"Protection and Human Rights", "Gender"}, "human_rights"),
    ({"Safety and Security", "Peacekeeping and Peacebuilding", "Mine Action"}, "conflict_security"),
    ({"Food and Nutrition", "Agriculture", "Humanitarian Financing"}, "economy_inequality"),
    ({"Climate Change and Environment", "Disaster Management"}, "disaster_climate"),
    ({"Education"}, "culture_society"),
]
# タイトルにこれらの語があれば「難民・移動」タグを付ける(分野を横断する視点のため)。
DISPLACEMENT_WORDS = ("refugee", "displace", "idp", "migrant", "asylum", "returnee")


def _names(items):
    """[{"name": ...}, ...] から name の集合を取り出す。"""
    return {i.get("name") for i in (items or []) if i.get("name")}


def categorize(themes: set, disaster_types: set) -> str:
    """レポートのテーマと災害種別から 8 分野のどれかを決める。

    判定の順: 疫病などの災害種別 → それ以外の災害は「災害・気候」→ テーマ。
    どれにも当たらなければ人道状況の報告とみなして「人権」に入れる(AI 段階で見直す)。
    """
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
    """API の応答を Event のリストに変換する(通信なし、テスト可能)。"""
    out = []
    for item in doc.get("data", []):
        f = item.get("fields") or {}
        formats = _names(f.get("format"))
        if formats and formats <= SKIP_FORMATS:
            continue  # 地図だけ・図表だけのものは除く(本文のあるレポートは残す)
        themes = _names(f.get("theme"))
        dtypes = _names(f.get("disaster_type"))
        title = f.get("title") or ""
        pc = f.get("primary_country") or {}
        loc = pc.get("location") or {}
        tags = []
        if "Shelter and Non-Food Items" in themes or any(w in title.lower() for w in DISPLACEMENT_WORDS):
            tags.append("displacement")
        # 復興・再建のテーマなら「改善」。改善の兆しも悪化と同じ重さで集める方針。
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
                confidence="medium",  # 編集済みの二次情報(各団体の報告を集約したもの)
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
    """ReliefWeb の日時(タイムゾーン付き)を UTC の ISO 8601 にそろえる。"""
    if not s:
        return None
    # "2026-11-07T10:20:30+00:00"
    return iso_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))


def query(since: datetime) -> dict:
    """since 以降に作られたレポートを新しい順に取る検索条件を作る。"""
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
    """直近 lookback_hours 時間(既定 26 時間)のレポートを取得する。

    日次実行に対して 26 時間と少し長めに取るのは、実行時刻のずれで取りこぼさない
    ため。重なった分は同じ id なので保存時に上書きされ、重複しない。
    """
    appname = os.environ.get("RELIEFWEB_APPNAME")
    if not appname:
        # The public website and its RSS feeds sit behind bot protection
        # (they answer automated clients with an empty HTTP 202), so the
        # approved API is the only route we use.
        # 公開サイトや RSS はボット対策で空の応答を返すため使えない。
        # 承認済み API だけが経路なので、未登録ならスキップする。
        raise http.SourceSkipped(
            "RELIEFWEB_APPNAME is not set. ReliefWeb only answers approved appnames: "
            "request one at https://apidoc.reliefweb.int/parameters#appname")
    since = datetime.now(timezone.utc) - timedelta(hours=ctx.lookback_hours)
    return parse(http.post_json(API_URL.format(appname=appname), query(since)))
