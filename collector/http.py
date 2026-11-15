"""Tiny HTTP helper on the standard library (no dependencies to keep CI fast).

標準ライブラリだけで書いた小さな HTTP ヘルパー。外部ライブラリを使わないのは、
GitHub Actions でのインストールを不要にして CI を速く・壊れにくくするため。
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request

# 取得元の運営者が「誰がアクセスしているか」を分かるよう、名前と連絡先 URL を名乗る。
USER_AGENT = "present-for-kids-collector/0.1 (+https://github.com/sawagit/Present-for-Kids)"


class FetchError(RuntimeError):
    """取得に失敗した(通信エラー・HTTP エラー・空の応答)。そのソースは「失敗」扱い。"""


class SourceSkipped(RuntimeError):
    """A source is not configured yet (e.g. missing API key). Not a failure.

    ソースの準備ができていない(API キー未登録など)ことを表す。失敗ではなく
    「一時停止」として扱い、CI を赤くしない(例: ReliefWeb の appname 待ち)。
    """


def get(url: str, *, timeout: int = 60, retries: int = 3, data: bytes | None = None,
        headers: dict | None = None) -> bytes:
    """URL を取得して本文(bytes)を返す。

    - timeout=60 秒: GDELT の zip など大きめのファイルでも間に合う長さ。
    - retries=3 回: 一時的な障害は待てば直ることが多いので、間隔を 2, 4, 8 秒と
      倍々に伸ばしてやり直す(相手のサーバーに負荷をかけないため)。
    - data を渡すと POST になる。
    """
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    hdrs.update(headers or {})
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                # gzip 圧縮で返すサーバーがあるので、ここで展開しておく。
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)
                if not body:
                    # Say what came back instead of failing later on "no element found".
                    raise FetchError(
                        f"empty body from {url} (HTTP {resp.status}, "
                        f"Content-Type={resp.headers.get('Content-Type')!r}, final URL={resp.url})")
                return body
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            last = FetchError(f"HTTP {e.code} for {url}: {body}")
            if 400 <= e.code < 500 and e.code != 429:
                break  # client error: retrying will not help
                # 4xx(権限なし・URL 誤りなど)は何度やっても同じなのでやめる。
                # ただし 429(アクセス過多)は待てば通るのでやり直す。
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = FetchError(f"{type(e).__name__} for {url}: {e}")
        time.sleep(2 ** attempt * 2)
    raise last


def get_json(url: str, **kw):
    """GET して JSON として読む。"""
    return json.loads(get(url, **kw).decode("utf-8"))


def post_json(url: str, payload: dict, **kw):
    """JSON を POST して、応答を JSON として読む(ReliefWeb API の検索で使う)。"""
    body = json.dumps(payload).encode("utf-8")
    raw = get(url, data=body, headers={"Content-Type": "application/json"}, **kw)
    return json.loads(raw.decode("utf-8"))
