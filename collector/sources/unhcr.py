"""UNHCR Refugee Data Finder: forcibly displaced people by country of origin.

API: https://api.unhcr.org/docs/refugee-statistics.html (free, attribution).
This is annual statistics, so it produces Indicators, not Events. It runs
weekly; the values only change when UNHCR publishes a new year.

UNHCR(国連難民高等弁務官事務所)の、出身国別の難民・避難民の数。年次の統計
なので「出来事」ではなく「指標(Indicator)」として保存する。値が変わるのは
UNHCR が新しい年の数字を公表したときだけなので、週 1 回の取得で十分。
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
# API の項目名 → 保存する指標名(末尾に _by_origin を付けて「出身国別」と分かるようにする)
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
    """数値に変換する。"-" や空欄は「データなし」なので None(0 とは区別する)。"""
    if v in (None, "", "-"):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse(items: list[dict], year: int) -> list[dict]:
    """API の行を指標のリストに変換する(通信なし、テスト可能)。"""
    out = []
    for it in items:
        iso3 = it.get("coo_iso") or it.get("coo")
        if not iso3 or iso3 == "-":
            continue  # 出身国が不明な行は国と結びつけられないので使わない
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
    """1 年分を全ページ取得する(1 ページ 1000 件、最大ページ数は応答に含まれる)。"""
    items, page, max_pages = [], 1, 1
    while page <= max_pages:
        doc = http.get_json(API_URL.format(page=page, year=year))
        items.extend(doc.get("items") or [])
        max_pages = int(doc.get("maxPages") or 1)
        page += 1
    return items


def fetch(ctx) -> list[dict]:
    """データのある最新の年を探して取得する。

    統計の公表は翌年の夏ごろなので、今年の分はまだないことが多い。今年から
    さかのぼって最大 4 年分を試し、最初に見つかった年を使う。
    """
    this_year = datetime.now(timezone.utc).year
    for year in range(this_year, this_year - 4, -1):
        items = _fetch_year(year)
        if items:
            print(f"[unhcr] latest year with data: {year} ({len(items)} origins)", file=sys.stderr)
            return parse(items, year)
    raise http.FetchError("UNHCR returned no data for the last 4 years")
