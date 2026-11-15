"""Country and region lookups, so events from every source can be joined.

Every source names places differently: GDACS and ReliefWeb give ISO3 codes,
GDELT gives FIPS 10-4 codes, USGS gives free text ("12 km N of X, Chile").
To link crises across sources we resolve all of them to one ISO3 code and
add the UN M49 region and subregion (e.g. Africa / Eastern Africa), which is
the level where spill-over effects (refugees, prices, epidemics) are visible.

The table is collector/countries.json (regenerate with tools/build_countries.py).

国と地域の対応づけ。ソースごとに場所の書き方が違う(GDACS と ReliefWeb は ISO3、
GDELT は FIPS コード、USGS は "12 km N of X, Chile" のような文章)ので、すべてを
1 つの ISO3 国コードにそろえ、国連 M49 の地域・小地域(例: アフリカ / 東アフリカ)
を付ける。難民・物価・感染症のような「波及」は国境を越えて小地域の単位で見える
ことが多いので、つながりの分析にはこの粒度が役に立つ。
対応表は collector/countries.json(tools/build_countries.py で再生成)。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

TABLE = Path(__file__).with_name("countries.json")

# M49 region names in Japanese (the site is bilingual from the start).
# 地域名の日本語訳(サイトは最初から日英対応のため)。
REGION_JA = {
    "Africa": "アフリカ",
    "Americas": "南北アメリカ",
    "Asia": "アジア",
    "Europe": "ヨーロッパ",
    "Oceania": "オセアニア",
    "Northern Africa": "北アフリカ",
    "Eastern Africa": "東アフリカ",
    "Middle Africa": "中部アフリカ",
    "Southern Africa": "南部アフリカ",
    "Western Africa": "西アフリカ",
    "Caribbean": "カリブ海地域",
    "Central America": "中央アメリカ",
    "South America": "南アメリカ",
    "Northern America": "北アメリカ",
    "Central Asia": "中央アジア",
    "Eastern Asia": "東アジア",
    "South-eastern Asia": "東南アジア",
    "Southern Asia": "南アジア",
    "Western Asia": "西アジア",
    "Eastern Europe": "東ヨーロッパ",
    "Northern Europe": "北ヨーロッパ",
    "Southern Europe": "南ヨーロッパ",
    "Western Europe": "西ヨーロッパ",
    "Australia and New Zealand": "オーストラリア・ニュージーランド",
    "Melanesia": "メラネシア",
    "Micronesia": "ミクロネシア",
    "Polynesia": "ポリネシア",
}

# 米国の州名。USGS は米国内の地震を "X, Alaska" のように州名で書くため。
# "Georgia" は国(ジョージア)と同名なので、州の一覧からは外してある。
US_STATES = (
    "Alabama Alaska Arizona Arkansas California Colorado Connecticut Delaware Florida "
    "Hawaii Idaho Illinois Indiana Iowa Kansas Kentucky Louisiana Maine Maryland "
    "Massachusetts Michigan Minnesota Mississippi Missouri Montana Nebraska Nevada "
    "New_Hampshire New_Jersey New_Mexico New_York North_Carolina North_Dakota Ohio Oklahoma "
    "Oregon Pennsylvania Rhode_Island South_Carolina South_Dakota Tennessee Texas Utah "
    "Vermont Virginia Washington West_Virginia Wisconsin Wyoming"
).replace("_", " ").split()
# 州の略号。USGS は "X, CA"(カリフォルニア)のように略号でも書く。
US_ABBR = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV "
    "NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY"
).split()

# Names in USGS / GDACS text that differ from CLDR names.
# 対応表の正式な英語名と違う書き方(通称・旧称・島の名前など)の読み替え。
# 島の名前は、その島が属する国に対応づける(例: スマトラ島 → インドネシア)。
ALIASES = {
    "russia": "RUS", "iran": "IRN", "syria": "SYR", "laos": "LAO", "vietnam": "VNM",
    "burma": "MMR", "myanmar": "MMR", "south korea": "KOR", "north korea": "PRK",
    "taiwan": "TWN", "tanzania": "TZA", "bolivia": "BOL", "venezuela": "VEN",
    "moldova": "MDA", "turkey": "TUR", "turkiye": "TUR", "türkiye": "TUR",
    "czech republic": "CZE", "czechia": "CZE", "ivory coast": "CIV",
    "cote d'ivoire": "CIV", "côte d’ivoire": "CIV", "democratic republic of the congo": "COD",
    "dr congo": "COD", "congo": "COG", "republic of the congo": "COG",
    "palestine": "PSE", "gaza": "PSE", "west bank": "PSE", "kosovo": "XKX",
    "east timor": "TLS", "timor-leste": "TLS", "cabo verde": "CPV", "cape verde": "CPV",
    "eswatini": "SWZ", "swaziland": "SWZ", "micronesia": "FSM", "brunei": "BRN",
    "united states": "USA", "usa": "USA", "puerto rico": "PRI", "united kingdom": "GBR",
    "uk": "GBR", "sumatera": "IDN", "sumatra": "IDN", "java": "IDN", "sulawesi": "IDN",
    "mindanao": "PHL", "luzon": "PHL", "honshu": "JPN", "hokkaido": "JPN", "kyushu": "JPN",
    "new britain": "PNG", "new ireland": "PNG", "kuril islands": "RUS", "kamchatka": "RUS",
    "south sandwich islands": "SGS", "kermadec islands": "NZL", "fiji islands": "FJI",
}


@lru_cache(maxsize=1)
def _tables():
    """対応表を読み、ISO3・FIPS・名前で引ける辞書を作る(初回だけ読み、以後は使い回す)。"""
    rows = json.loads(TABLE.read_text(encoding="utf-8"))
    by_iso3 = {r["iso3"]: r for r in rows}
    by_fips = {f: r["iso3"] for r in rows for f in r["fips"]}
    by_name = {}
    for r in rows:
        by_name.setdefault(r["name_en"].lower(), r["iso3"])
    for s in US_STATES:
        by_name[s.lower()] = "USA"
    for a in US_ABBR:
        by_name[a.lower()] = "USA"
    by_name.update(ALIASES)
    # "Georgia" is left to the country: USGS writes US places with state codes ("GA").
    # 読み替え(ALIASES)は最後に入れて、正式名より優先させる。
    return by_iso3, by_fips, by_name


def country(iso3: str | None) -> dict | None:
    """ISO3 から国の情報(日英の国名・地域など)を返す。知らないコードなら None。"""
    return _tables()[0].get(iso3) if iso3 else None


def iso3_from_fips(fips: str | None) -> str | None:
    """FIPS 国コード(GDELT の形式、英国 = UK、ドイツ = GM など)を ISO3 に変換する。"""
    return _tables()[1].get(fips) if fips else None


def iso3_from_name(name: str | None) -> str | None:
    """国名・州名・島の名前などから ISO3 を求める。分からなければ None(推測しない)。

    まず名前をそのまま引き、だめなら "Fiji region" の region、"off the coast of X"
    の前置き、"northern" のような方角を取り除いて、もう一度だけ引く。
    """
    if not name:
        return None
    n = re.sub(r"\s+", " ", name).strip().lower()
    by_name = _tables()[2]
    if n in by_name:
        return by_name[n]
    # "Fiji region", "northern Sumatra", "off the coast of X", "near the coast of X"
    n = re.sub(r"\s+region$", "", n)
    n = re.sub(r"^(?:.*\bof\s+)", "", n)
    n = re.sub(r"^the\s+", "", n)
    n = re.sub(r"^(?:northern|southern|eastern|western|central|north|south|east|west)\s+", "", n)
    return by_name.get(n)


def iso3_from_place(place: str | None) -> str | None:
    """Best effort for free text such as USGS's "120 km SSW of Town, Indonesia".

    Returns None for open ocean ("Mid-Atlantic Ridge"): an event there is not
    in any country, and we never guess.

    USGS の地名の文章から国を求める。国名はたいてい最後のカンマの後にあるので
    そこだけを見る。海嶺や大洋の地震はどの国にも属さないので None を返し、
    無理に近くの国へ割り当てることはしない。
    """
    if not place:
        return None
    last = place.rsplit(",", 1)[-1]
    return iso3_from_name(last)


def regions(iso3: str | None) -> tuple[str | None, str | None]:
    """ISO3 から (地域, 小地域) を返す。国が分からなければ (None, None)。"""
    c = country(iso3)
    return (c["region"], c["subregion"]) if c else (None, None)
