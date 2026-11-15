"""One module per data source. Each exposes NAME, KIND and fetch(ctx) -> list[dict].

データソースごとに 1 モジュール。どのモジュールも同じ形を持つ:
- NAME: ソース名(保存先フォルダ名にもなる)
- KIND: "events"(出来事)か "indicators"(統計値)
- CADENCE: 取得頻度 "hourly" / "daily" / "weekly"
- fetch(ctx): 取得して共通形式のリストを返す
新しいソースを足すときは、この形のモジュールを作って下の SOURCES に加えるだけでよい。
"""

from . import gdacs, gdelt, reliefweb, unhcr, usgs

SOURCES = {m.NAME: m for m in (usgs, gdacs, gdelt, reliefweb, unhcr)}
