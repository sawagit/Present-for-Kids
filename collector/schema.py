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

全ソース共通のデータの形(スキーマ)を決めるモジュール。サイトの表示も分析も
この形を前提にするので、ここが「契約」になる。
- Event(出来事): ある場所・ある時刻に起きたこと。data/events/ に保存。
- Indicator(指標): ある地域・期間の数値(例: ある国の年間の難民数)。data/indicators/ に保存。
- 出来事どうしの因果リンクは、この後の AI 段階で作る(Phase 0 には含まない)。
- タイトルと要約は最初から日英 2 言語の形 {"en": ..., "ja": ...}。ソースは英語
  なので、日本語は AI 段階で埋めるまで None。
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import geo

# 2 (2026-11-15): location.region / location.subregion added (UN M49 names),
#   country_iso3 filled for GDELT and USGS, stable GDELT ids. Only additions:
#   readers of version 1 keep working. Older files are upgraded in place by
#   upgrade_event() the next time the collector runs.
# データ形式のバージョン。形を変えたら上げる。2 では地域の項目を追加し、GDELT の
# ID を安定したものに変えた。追加だけなので、バージョン 1 を読むコードも動き続ける。
# 古いファイルは次の収集時に upgrade_event() で自動的に更新される。
SCHEMA_VERSION = 2

# Layer 1 categories, all treated equally (decision 2026-11-01).
# 8 つの分野。特定の分野を優先せず、すべて同じ重さで扱う(2026-11-01 の決定)。
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
# 分野を横断するタグ。難民・移動は紛争・災害・経済のどれからも生じるので、分野
# ではなくタグにして、どの分野の出来事にも付けられるようにしている。
TAGS = ("displacement",)

# Improvements are first-class events, not an afterthought (site vision).
# 出来事の向き: 悪化 / 改善 / 転換点(停戦など)/ 中立。改善も悪化と同じように
# 集めて見せる(恐怖を煽らず、改善の兆しも伝えるというサイトの方針)。
DIRECTIONS = ("worsening", "improving", "turning_point", "neutral")

# 深刻度の 4 段階。ソースごとの尺度(PAGER、GDACS の警報、記事数)をここにそろえる。
SEVERITY_LEVELS = ("low", "medium", "high", "critical")

# How much we trust that the event itself happened as described.
# high = authoritative primary source (USGS, GDACS, UN agencies)
# medium = curated secondary source (ReliefWeb reports)
# low = machine-coded from news (GDELT)
# 信頼度: その出来事が書かれた通りに起きたと、どれだけ信じられるか。
# high = 公的機関の一次情報、medium = 編集された二次情報、low = 報道からの機械抽出。
CONFIDENCE = ("high", "medium", "low")

# Coordinates are rounded so we never point at a house or a person
# (privacy policy in the planning doc: city level at most). 2 decimals ≈ 1 km.
# 座標は小数 2 桁(約 1 km)に丸める。特定の家や個人を指さないためのプライバシー方針。
COORD_DECIMALS = 2


def iso_utc(dt: datetime) -> str:
    """日時を "2026-11-15T12:00:00Z" の形(UTC)にする。全ソースの時刻をこの形にそろえる。

    タイムゾーンのない日時は UTC とみなす。
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_epoch_ms(ms: int | float) -> str:
    """ミリ秒単位の UNIX 時刻(USGS の形式)を iso_utc の形にする。"""
    return iso_utc(datetime.fromtimestamp(ms / 1000, tz=timezone.utc))


def round_coord(v):
    """座標を COORD_DECIMALS 桁に丸める。空や数値でないものは None。"""
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
    """共通形式の Event を作り、検証してから返す。全ソースはこの関数を通して作る。

    - id は "ソース名:ソース内の ID"。ソースをまたいでも重ならない。
    - 国コード(ISO3)から国連の地域・小地域を自動で付ける。国が分かれば地域も
      分かるので、ソース側は国コードだけ渡せばよい。
    - 契約を破る値(未知の分野、出典 URL なしなど)は ValueError になる。呼び出し側は
      その件だけ飛ばす。
    """
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
    """Bring a stored event up to SCHEMA_VERSION (idempotent, never drops data).

    保存済みの古い形式の Event を、現在の形式に更新する。何度実行しても結果は
    同じで、データを消すことはない(重複の統合は store.py 側で行う)。
    """
    if ev.get("schema", 1) >= SCHEMA_VERSION:
        return ev  # すでに最新なら何もしない
    loc = ev["location"]
    iso3 = loc.get("country_iso3")
    # 国コードがなければ、ソースごとの手がかりから求める
    # (GDELT は FIPS 国コード、USGS は地名のテキスト)。
    if not iso3 and ev["source"] == "gdelt":
        iso3 = geo.iso3_from_fips(ev["attrs"].get("country_fips"))
    if not iso3 and ev["source"] == "usgs":
        iso3 = geo.iso3_from_place(loc.get("place"))
    loc["country_iso3"] = iso3
    loc["region"], loc["subregion"] = geo.regions(iso3)
    # GDELT は ID の作り方を変えたので、記録済みの種類・場所・日付から作り直す。
    # 同じ出来事の古い重複は同じ ID になり、保存時に 1 件にまとまる。
    if ev["source"] == "gdelt":
        from .sources import gdelt  # local import: sources import this module
        sid = gdelt.stable_id(ev["attrs"].get("cameo_code", "")[:3], loc.get("place") or "",
                              ev["occurred_at"][:10].replace("-", ""))
        ev["id"], ev["source_id"] = f"gdelt:{sid}", sid
    ev["schema"] = SCHEMA_VERSION
    return ev


def validate_event(ev: dict) -> None:
    """Raise ValueError if an event breaks the contract the site relies on.

    サイトが前提にしている約束を Event が守っているか確かめる。決められた値の
    一覧にない分野・向き・信頼度・深刻度・タグ、UTC でない時刻、出典 URL なし、
    英語タイトルなしは不正とする。問題はまとめて 1 つのエラーで報告する。
    """
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
        # 出典のない情報は載せない(根拠のない主張をしないための大原則)。
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
    """共通形式の Indicator(統計値)を作る。

    id は "ソース:指標名:国:期間" なので、同じ指標を取り直すと上書きされる。
    """
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
