"""GDELT 2.0 Events (machine-coded from world news, every 15 minutes; free).

GDELT is huge and noisy (its own dashboard is a "prototype" with many false
positives), so we keep only a small, filtered slice:

* root events only (IsRootEvent = 1), with a resolvable action location
* CAMEO root codes that matter for crises, and also the cooperative ones
  (aid, ceasefire, easing) so improvements are collected with the same weight
* at least MIN_ARTICLES articles, duplicates merged, capped to the top N
* only events dated within MAX_AGE_DAYS of when the news reported them
  (articles often recall old events, which GDELT then codes with the old date)
* at most MAX_PER_COUNTRY events per country, so English-language news about
  one country (mostly the US) cannot crowd out the rest of the world

The category mapping below is deliberately coarse. The AI stage refines it.
No article text is stored: only the source URL and GDELT's coded metadata.

Format: http://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf

GDELT は世界中のニュースから「誰が・どこで・何をしたか」を 15 分ごとに機械的に
抽出したデータ。量が膨大で誤検出も多いため、次の絞り込みをかけて小さく使う:

- 根となる出来事(IsRootEvent=1)で、場所が特定できるものだけ
- 危機に関わる種類(抗議・弾圧・攻撃・戦闘など)に加え、支援・停戦・譲歩といった
  「改善」の種類も同じ重さで集める(悪いことだけを並べないというサイトの方針)
- 記事数が MIN_ARTICLES 以上。同じ出来事の重複はまとめ、上位 MAX_EVENTS 件に絞る
- 報道日から MAX_AGE_DAYS 日以内の出来事だけ(記事が過去の出来事に触れると、
  GDELT はその古い日付で出来事を作ってしまうため)
- 1 か国あたり MAX_PER_COUNTRY 件まで(英語報道の多い米国などに偏らないように)

分野の対応づけはわざと大まかにしてあり、AI 段階で精緻にする。記事本文は保存せず、
URL と GDELT が付けたメタデータだけを持つ。信頼度は常に low(機械抽出のため、
事実として断定して見せない)。
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone

from .. import geo, http
from ..schema import iso_utc, make_event

NAME = "gdelt"
KIND = "events"
CADENCE = "daily"
LASTUPDATE_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
SLICE_URL = "http://data.gdeltproject.org/gdeltv2/{ts}.export.CSV.zip"

# Column positions in the 61-column export file.
# 61 列のタブ区切りファイルの、使う列の位置(GDELT のコードブックに従う)。
C = dict(
    id=0, sqldate=1, actor1=6, actor1_cc=7, actor2=16, actor2_cc=17,
    is_root=25, code=26, base_code=27, root_code=28, quad=29, goldstein=30,
    mentions=31, sources=32, articles=33, tone=34,
    geo_type=51, geo_name=52, geo_cc=53, geo_lat=56, geo_lon=57, geo_fid=58,
    date_added=59, url=60,
)
NCOLS = 61

# root code -> (category, direction, label)
# CAMEO の大分類コード → (分野, 方向, 表示名)。ここにないコードは捨てる。
# 06〜08 は協力・支援・譲歩で「改善」、14〜20 は抗議・弾圧・攻撃で「悪化」。
ROOTS = {
    "06": ("politics_governance", "improving", "Material cooperation"),
    "07": ("economy_inequality", "improving", "Aid provided"),
    "08": ("politics_governance", "improving", "Concession / easing"),
    "14": ("culture_society", "worsening", "Protest"),
    "17": ("human_rights", "worsening", "Coercion / repression"),
    "18": ("conflict_security", "worsening", "Assault"),
    "19": ("conflict_security", "worsening", "Armed clash"),
    "20": ("conflict_security", "worsening", "Mass violence"),
}
# Finer overrides by base code.
# 中分類で上書きするもの。例えば 087(軍事的な緊張緩和・停戦)は大分類 08 の
# 「譲歩」より意味が重いので、紛争分野の「転換点」として扱う。
BASES = {
    "087": ("conflict_security", "turning_point", "Military de-escalation / ceasefire"),
    "073": ("economy_inequality", "improving", "Humanitarian aid"),
    "075": ("human_rights", "improving", "Protection granted"),
    "083": ("politics_governance", "improving", "Political reform accepted"),
}

# 設定値はすべて環境変数で変えられる(コードを変えずに調整できるように)。
# 記事 5 本未満は誤検出や地域のささいな話題が多いので捨てる。
MIN_ARTICLES = int(os.environ.get("GDELT_MIN_ARTICLES", "5"))
# 1 回の取得で残す最大件数。リポジトリ内の JSON が大きくなりすぎないための上限。
MAX_EVENTS = int(os.environ.get("GDELT_MAX_EVENTS", "300"))
# News often mentions old events ("a year ago..."); GDELT then codes them with
# the old date. Keep only events dated within this many days of being seen.
# We collect daily, so 3 days leaves room for late reporting and time zones.
# 報道日と出来事の日付の差がこの日数を超えたら「過去の出来事への言及」とみなして
# 捨てる。日次収集なので 3 日あれば、遅れて届く報道や時差の分の余裕は十分ある。
MAX_AGE_DAYS = int(os.environ.get("GDELT_MAX_AGE_DAYS", "3"))
# 1 か国あたりの上限。MAX_EVENTS(300)の 1 割にして、少なくとも 10 か国以上が
# 並ぶようにする。英語報道が多い国だけで埋まるのを防ぐための編集上の判断。
MAX_PER_COUNTRY = int(os.environ.get("GDELT_MAX_PER_COUNTRY", "30"))

# Why rows were dropped in the last fetch (reported in data/status.json).
# 直近の取得で行を捨てた理由ごとの件数。status.json に記録し、絞り込みが効きすぎて
# いないかを後から確かめられるようにする。
LAST_STATS: dict = {}


def _too_old(r) -> bool:
    """出来事の日付が、報道で見つかった日より MAX_AGE_DAYS 日以上前なら True。

    日付が読めない行も信用できないので True(捨てる)にする。
    """
    try:
        event = datetime.strptime(r[C["sqldate"]], "%Y%m%d")
        seen = datetime.strptime(r[C["date_added"]][:8], "%Y%m%d")
    except ValueError:
        return True
    return (seen - event).days > MAX_AGE_DAYS


def stable_id(base_code: str, place: str, sqldate: str) -> str:
    """Same kind of event, same place, same day -> same id on every run.

    GDELT gives every news mention its own row id, so the "first" row of a
    merged group differs from run to run; keying on what the event is keeps
    the daily re-fetch from creating duplicates.

    「同じ種類・同じ場所・同じ日」の出来事には、何度取得しても同じ ID を付ける。
    GDELT は記事ごとに別の行 ID を振るため、行 ID を使うと毎日の再取得で同じ
    出来事が別物として増えてしまう(以前の重複の原因)。場所の名前は長いので
    ハッシュの先頭 8 文字にする。
    """
    h = hashlib.sha1(place.encode("utf-8")).hexdigest()[:8]
    return f"{sqldate}-{base_code}-{h}"


def _level(root, articles):
    """深刻度を決める。報道の量(記事数)を、どれだけ注目されているかの目安にする。

    大量殺害(コード 20)か記事 100 本以上 = high、25 本以上 = medium、それ未満 = low。
    機械抽出なので critical は付けない(断定を避けるため)。
    """
    if root == "20" or articles >= 100:
        return "high"
    if articles >= 25:
        return "medium"
    return "low"


def _reject(r) -> str | None:
    """Why a row is dropped (None = keep). Cheap, applied while streaming.

    行を捨てる理由を返す(None なら残す)。ファイルを読みながら 1 行ずつ判定する
    ので、メモリに全行を持たずに済む。軽い判定から順に並べてある。
    """
    if len(r) < NCOLS or r[C["is_root"]] != "1":
        return "not_root"
    if r[C["base_code"]] not in BASES and r[C["root_code"]] not in ROOTS:
        return "other_code"
    if not r[C["geo_lat"]] or not r[C["url"]]:
        return "no_location"
    try:
        if int(r[C["articles"]]) < MIN_ARTICLES:
            return "few_articles"
    except ValueError:
        return "few_articles"
    if _too_old(r):
        return "old_event"
    return None


def _keep(r) -> bool:
    """行を残すなら True。"""
    return _reject(r) is None


def parse_rows(rows) -> list[dict]:
    """Filter and merge raw export rows into events (pure function, testable).

    行を絞り込み、同じ出来事をまとめて Event にする(通信なし、テスト可能)。
    手順: 1) 同じ「種類・場所・日」の行をまとめ、記事数を合算する
          2) 記事数の多い順に並べ、国ごとの上限を守りながら上位を選ぶ
          3) 共通形式の Event に変換する
    """
    groups: dict[tuple, dict] = {}
    for r in rows:
        if not _keep(r):
            continue
        spec = BASES.get(r[C["base_code"]]) or ROOTS[r[C["root_code"]]]
        articles = int(r[C["articles"]])
        # Same kind of event at the same place on the same day = one event.
        # 同じ種類・同じ場所・同じ日 = 同じ出来事。複数の記事が同じ出来事を報じると
        # GDELT では別々の行になるので、ここで 1 件にまとめる。
        key = (r[C["base_code"]], r[C["geo_name"]], r[C["sqldate"]])
        g = groups.get(key)
        if g is None:
            groups[key] = {"row": r, "spec": spec, "articles": articles,
                           "urls": [r[C["url"]]], "ids": [r[C["id"]]]}
        else:
            g["articles"] += articles
            g["ids"].append(r[C["id"]])
            if r[C["url"]] not in g["urls"]:
                g["urls"].append(r[C["url"]])
            # 代表の行は記事数がいちばん多いもの(タイトルや座標はこの行から取る)。
            if articles > int(g["row"][C["articles"]]):
                g["row"] = r

    # 記事数の多い順に選ぶ。上限に達した国の出来事は飛ばして、他の国に枠を回す。
    ranked, per_country = [], {}
    for g in sorted(groups.values(), key=lambda g: g["articles"], reverse=True):
        cc = g["row"][C["geo_cc"]]
        if per_country.get(cc, 0) >= MAX_PER_COUNTRY:
            continue
        per_country[cc] = per_country.get(cc, 0) + 1
        ranked.append(g)
        if len(ranked) >= MAX_EVENTS:
            break
    out = []
    for g in ranked:
        r, (category, direction, label) = g["row"], g["spec"]
        actors = " / ".join(a for a in (r[C["actor1"]], r[C["actor2"]]) if a)
        place = r[C["geo_name"]]
        title = f"{label}: {actors} ({place})" if actors else f"{label} ({place})"
        d = r[C["sqldate"]]
        try:
            out.append(make_event(
                source=NAME,
                source_id=stable_id(r[C["base_code"]], place, d),
                category=category,
                type_=f"cameo_{r[C['code']]}",
                occurred_at=f"{d[:4]}-{d[4:6]}-{d[6:8]}T00:00:00Z",
                updated_at=_ts(r[C["date_added"]]),
                title_en=title,
                urls=g["urls"][:5],  # 出典は 5 件まで(ファイルを大きくしすぎない)
                confidence="low",  # machine-coded from news; never shown as fact
                lat=r[C["geo_lat"]],
                lon=r[C["geo_lon"]],
                place=place,
                # GDELT の国コードは FIPS 形式(英国 = UK など)なので ISO3 に変換する。
                country_iso3=geo.iso3_from_fips(r[C["geo_cc"]]),
                severity_level=_level(r[C["root_code"]], g["articles"]),
                severity_value=g["articles"],
                severity_unit="articles",
                direction=direction,
                attrs={
                    "cameo_code": r[C["code"]],
                    "quad_class": int(r[C["quad"]] or 0),
                    "goldstein": float(r[C["goldstein"]] or 0),
                    "avg_tone": round(float(r[C["tone"]] or 0), 2),
                    "num_mentions": int(r[C["mentions"]] or 0),
                    "country_fips": r[C["geo_cc"]] or None,
                    "actor1_country": r[C["actor1_cc"]] or None,
                    "actor2_country": r[C["actor2_cc"]] or None,
                    "merged_event_ids": g["ids"][:20],
                },
            ))
        except ValueError as e:
            print(f"[gdelt] skip: {e}", file=sys.stderr)
    return out


def _ts(s):
    """GDELT の日時 "20261114081500" を ISO 8601 にする(読めなければ None)。"""
    try:
        return iso_utc(datetime.strptime(s, "%Y%m%d%H%M%S"))
    except (TypeError, ValueError):
        return None


def _rows_from_zip(raw: bytes):
    """zip の中のタブ区切りファイルを 1 行ずつ返す(全体をメモリに展開しない)。

    文字化けした行があっても止まらないよう、読めない文字は置き換える。
    """
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            with z.open(name) as fh:
                text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="")
                yield from csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)


def latest_slice(lastupdate_text: str) -> datetime:
    """lastupdate.txt から、最新の 15 分ファイルの時刻を読み取る。"""
    for line in lastupdate_text.splitlines():
        parts = line.split()
        if parts and parts[-1].endswith(".export.CSV.zip"):
            ts = parts[-1].rsplit("/", 1)[-1].split(".")[0]
            return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    raise ValueError("no export file in lastupdate.txt")


# 全スライスが取れなかったときに、時間をおいて最初からやり直す回数と待ち時間。
# 2026-11-15 の CI で、lastupdate.txt が示した最新ファイルを含む直近 4 個が全部
# HTTP 404 になり、40 秒後の再実行では取れた(GDELT 側の更新途中の一時的な状態)。
# 404 は http.get ではやり直さない(4xx は普通は直らない)ので、ここでまとめて待つ。
# 30 秒 × 2 回なら、更新が落ち着くのに十分で、日次の収集も CI もほとんど遅くならない。
ROUND_RETRIES = 2
ROUND_WAIT_SECONDS = 30


def _fetch_round(n_slices: int):
    """lastupdate.txt を読み、最新から n_slices 個のファイルを取って (rows, ok, missing, dropped) を返す。

    欠けたスライスは飛ばす(GDELT はときどきファイルを欠かすため)。
    """
    newest = latest_slice(http.get(LASTUPDATE_URL).decode("utf-8"))
    rows, ok, missing = [], 0, 0
    dropped: dict[str, int] = {}
    for i in range(n_slices):
        ts = (newest - timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S")
        try:
            raw = http.get(SLICE_URL.format(ts=ts), retries=2)
        except http.FetchError as e:
            missing += 1  # GDELT occasionally skips a slice
            print(f"[gdelt] missing slice {ts}: {e}", file=sys.stderr)
            continue
        for r in _rows_from_zip(raw):
            why = _reject(r)
            if why is None:
                rows.append(r)
            else:
                dropped[why] = dropped.get(why, 0) + 1
        ok += 1
    return rows, ok, missing, dropped


def fetch(ctx, *, sleep=time.sleep) -> list[dict]:
    """Download the latest `ctx.gdelt_slices` 15-minute files (96 = one day).

    最新から過去へ 15 分ずつさかのぼって、ctx.gdelt_slices 個のファイルを取る
    (日次実行では 96 個 = 24 時間分)。GDELT はときどきファイルを欠かすので、
    欠けたものは飛ばす。1 つも取れなかったときは GDELT 側の一時的な障害とみて
    ROUND_WAIT_SECONDS 秒待ってやり直し、ROUND_RETRIES 回やり直しても
    取れなかったときだけ失敗にする。sleep はテストで待ち時間を飛ばすための差し替え口。
    """
    for attempt in range(ROUND_RETRIES + 1):
        rows, ok, missing, dropped = _fetch_round(ctx.gdelt_slices)
        if ok > 0:
            break
        # 1 つも取れなかった: 最後の試行でなければ待ってから lastupdate.txt を読み直す。
        if attempt < ROUND_RETRIES:
            print(f"[gdelt] no slice available, retrying in {ROUND_WAIT_SECONDS}s "
                  f"({attempt + 1}/{ROUND_RETRIES})", file=sys.stderr)
            sleep(ROUND_WAIT_SECONDS)
    if ok == 0:
        raise http.FetchError("no GDELT slice could be downloaded")
    print(f"[gdelt] slices ok={ok} missing={missing} kept_rows={len(rows)} dropped={dropped}",
          file=sys.stderr)
    LAST_STATS.clear()
    LAST_STATS.update(slices_ok=ok, slices_missing=missing, kept_rows=len(rows), dropped_rows=dropped)
    return parse_rows(rows)
