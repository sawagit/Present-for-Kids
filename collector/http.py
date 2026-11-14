"""Tiny HTTP helper on the standard library (no dependencies to keep CI fast)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

USER_AGENT = "present-for-kids-collector/0.1 (+https://github.com/sawagit/Present-for-Kids)"


class FetchError(RuntimeError):
    pass


class SourceSkipped(RuntimeError):
    """A source is not configured yet (e.g. missing API key). Not a failure."""


def get(url: str, *, timeout: int = 60, retries: int = 3, data: bytes | None = None,
        headers: dict | None = None) -> bytes:
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    hdrs.update(headers or {})
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            last = FetchError(f"HTTP {e.code} for {url}: {body}")
            if 400 <= e.code < 500 and e.code != 429:
                break  # client error: retrying will not help
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = FetchError(f"{type(e).__name__} for {url}: {e}")
        time.sleep(2 ** attempt * 2)
    raise last


def get_json(url: str, **kw):
    return json.loads(get(url, **kw).decode("utf-8"))


def post_json(url: str, payload: dict, **kw):
    body = json.dumps(payload).encode("utf-8")
    raw = get(url, data=body, headers={"Content-Type": "application/json"}, **kw)
    return json.loads(raw.decode("utf-8"))
