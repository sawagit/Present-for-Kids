"""Regenerate collector/countries.json (run by hand, rarely; needs network + Babel).

    pip install babel
    curl -sSLO https://raw.githubusercontent.com/datasets/country-codes/main/data/country-codes.csv
    python tools/build_countries.py country-codes.csv > collector/countries.json

Only codes and region groupings are taken from the CSV (ISO 3166 codes, FIPS
10-4 codes used by GDELT, UN M49 regions). Country names in English and
Japanese come from Unicode CLDR via Babel (Unicode license).
"""

import csv
import json
import sys

from babel import Locale

EN, JA = Locale("en"), Locale("ja")

# Not in ISO 3166 but present in news data.
EXTRA = [
    {"iso3": "XKX", "iso2": "XK", "fips": ["KV"], "m49": None,
     "region": "Europe", "subregion": "Southern Europe"},
]
# FIPS codes that map to the same ISO country (GDELT uses them separately).
EXTRA_FIPS = {"PSE": ["GZ", "WE"], "SRB": ["RI"]}
# Wrong or ambiguous in the source CSV (BES shares "NL" with the Netherlands).
DROP_FIPS = {"BES": ["NL"]}


def clean(s):
    return (s or "").replace("\xa0", " ").strip()


def main(path):
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
        if inter and region in ("Africa", "Americas"):
            sub = inter
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
