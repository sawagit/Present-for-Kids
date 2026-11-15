"""Regenerate collector/countries.json (run by hand, rarely; needs network + Babel).

    pip install babel
    curl -sSLO https://raw.githubusercontent.com/datasets/country-codes/main/data/country-codes.csv
    python tools/build_countries.py country-codes.csv > collector/countries.json

Only codes and region groupings are taken from the CSV (ISO 3166 codes, FIPS
10-4 codes used by GDELT, UN M49 regions). Country names in English and
Japanese come from Unicode CLDR via Babel (Unicode license).

国と地域の対応表 collector/countries.json を作り直すスクリプト(手動で、まれに実行)。
CSV からはコードと地域区分(ISO 3166、GDELT が使う FIPS、国連 M49 の地域)だけを
使い、日英の国名は利用条件の明確な Unicode CLDR(Babel 経由)から取る。CSV の国名
の列は利用条件がはっきりしないものがあるため使わない。収集の本体はこの表を読む
だけなので、Babel やネットワークは不要のまま。
"""

import csv
import json
import sys

from babel import Locale

EN, JA = Locale("en"), Locale("ja")

# Not in ISO 3166 but present in news data.
# ISO の一覧にはないが、ニュースには出てくる国・地域(コソボ)を手で足す。
EXTRA = [
    {"iso3": "XKX", "iso2": "XK", "fips": ["KV"], "m49": None,
     "region": "Europe", "subregion": "Southern Europe"},
]
# FIPS codes that map to the same ISO country (GDELT uses them separately).
# GDELT はガザ(GZ)とヨルダン川西岸(WE)を別コードで書くが、ISO ではどちらも
# パレスチナ(PSE)。セルビアの FIPS(RI)は元の CSV で欠けているので補う。
EXTRA_FIPS = {"PSE": ["GZ", "WE"], "SRB": ["RI"]}
# Wrong or ambiguous in the source CSV (BES shares "NL" with the Netherlands).
# 元の CSV でオランダ領カリブ(BES)にもオランダと同じ "NL" が付いているので外す。
DROP_FIPS = {"BES": ["NL"]}


def clean(s):
    """CSV の値の前後の空白と、改行しない空白(\xa0、空欄の印に使われている)を除く。"""
    return (s or "").replace("\xa0", " ").strip()


def main(path):
    """CSV を読んで対応表を作り、標準出力に 1 行 1 国の JSON で書き出す。"""
    out = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        iso3 = clean(r["ISO3166-1-Alpha-3"])
        if not iso3:
            continue
        region = clean(r["Region Name"]) or None
        inter = clean(r["Intermediate Region Name"])
        sub = clean(r["Sub-region Name"]) or None
        # Africa and Latin America are split further by M49's intermediate regions,
        # which are closer to how crises spread (e.g. Eastern vs Western Africa).
        # M49 の小地域ではアフリカの大半が「サハラ以南」の 1 つにまとまってしまうので、
        # アフリカと中南米はもう一段細かい区分(東アフリカ・西アフリカなど)を使う。
        if inter and region in ("Africa", "Americas"):
            sub = inter
        # FIPS は 2 文字のものだけ使う(PSE の "GZWE" のような合成値は EXTRA_FIPS で補う)。
        fips = [f for f in [clean(r["FIPS"])] if f and len(f) == 2]
        fips = [f for f in fips if f not in DROP_FIPS.get(iso3, [])]
        fips += EXTRA_FIPS.get(iso3, [])
        out.append({"iso3": iso3, "iso2": clean(r["ISO3166-1-Alpha-2"]) or None, "fips": fips,
                    "m49": clean(r["M49"]) or None, "region": region, "subregion": sub})
    out += EXTRA
    for c in out:
        c["name_en"] = EN.territories.get(c["iso2"]) or c["iso3"]
        c["name_ja"] = JA.territories.get(c["iso2"])
    out.sort(key=lambda c: c["iso3"])
    print("[")
    print(",\n".join(json.dumps(c, ensure_ascii=False, sort_keys=True) for c in out))
    print("]")


if __name__ == "__main__":
    main(sys.argv[1])
