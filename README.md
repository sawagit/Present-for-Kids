# Present for Kids

世界で起きている危機(紛争、災害、人権、経済・格差、公衆衛生、文化・分断、AI・技術、政治)が、互いにどうつながっているかを俯瞰するための非営利サイトです。存在価値は「未来の子供たちを幸せにするための理解を得ること」です。

A non-profit site that shows how the world's crises (conflict, disasters, human rights, economy and inequality, public health, culture and division, AI and technology, politics) connect to each other. Its purpose: *understanding that helps make the future better for children.*

## 現在の段階 / Status

Phase 0: データ収集の基盤。サーバーとデータベースを持たず、GitHub Actions が公開データを取得して `data/` に静的 JSON として保存します(月額 0 円)。AI による分類・日英要約・因果リンク抽出は次の段階です。

Phase 0: the collection pipeline. No server, no database: GitHub Actions fetches open data and commits static JSON under `data/`. AI classification, Japanese/English summaries and cause-effect links come next.

## データソース / Sources

| source | 内容 / what | 頻度 / cadence | 種類 / kind | ライセンス / license |
|---|---|---|---|---|
| [USGS](https://earthquake.usgs.gov/earthquakes/feed/) | M4.5 以上の地震 / earthquakes M4.5+ | hourly | events | public domain |
| [GDACS](https://www.gdacs.org/) | 災害アラート(台風・洪水・火山・干ばつ・山火事) / disaster alerts | hourly | events | free, attribution |
| [GDELT 2.0](https://www.gdeltproject.org/) | 報道から機械抽出した紛争・抗議・支援・停戦 / news-coded events incl. aid and ceasefires | daily | events | free, attribution |
| [ReliefWeb](https://reliefweb.int/) | 人道危機レポート(タイトルとリンクのみ) / humanitarian reports (title + link only) | daily | events | per publisher |
| [UNHCR](https://www.unhcr.org/refugee-statistics/) | 出身国別の難民・避難民数 / displaced people by origin | weekly | indicators | free, attribution |

記事や報告書の本文は保存しません。メタデータとリンクだけを持ちます。座標は約 1 km に丸めます。
We never store article or report bodies, only metadata and links. Coordinates are rounded to about 1 km.

## データ形式 / Data layout

```
data/
  events/<source>/<YYYY-MM>.json      # Event の配列(1 行 1 件)
  indicators/<source>/<period>.json   # Indicator の配列
  status.json                         # ソースごとの最終実行結果
```

Event の主なフィールド / main Event fields (see `collector/schema.py`):

- `category`: 8 分野のどれか / one of 8 categories
- `direction`: `worsening` / `improving` / `turning_point` / `neutral`(改善も同じ重さで集める / improvements are collected too)
- `title`, `summary`: `{"en": ..., "ja": ...}`(日本語は AI 段階で埋める / Japanese is filled by the AI stage)
- `confidence`: `high`(一次機関)/ `medium`(編集済み二次)/ `low`(報道からの機械抽出)
- `location`, `severity`, `urls`(出典は必須 / a source link is required), `attrs`

## 動かし方 / Running locally

Python 3.11+ だけで動きます(外部ライブラリなし)。/ Standard library only.

```sh
python -m unittest discover -s tests            # オフラインのテスト / offline tests
python -m collector.run --all --dry-run         # 全ソースを取得して検証、書き込みなし
python -m collector.run --cadence hourly        # usgs + gdacs を data/ に保存
```

## 自動実行 / Automation

- `.github/workflows/ci.yml`: プッシュと PR ごとにテストと全ソースの取得検証 / tests + live fetch check on every push and PR
- `.github/workflows/collect.yml`: 毎時・日次・週次で収集し `data/` をコミット。失敗したソースがあると実行が赤くなり、GitHub からメールが届きます / scheduled collection; a failed source turns the run red and GitHub emails the owner

ReliefWeb の API は承認済みの `appname` が必要です([申請フォーム](https://apidoc.reliefweb.int/parameters#appname))。承認されたらリポジトリの Secrets に `RELIEFWEB_APPNAME` として登録してください。それまでは承認不要の公開 RSS から取得します(国やテーマの情報は少なめ)。
ReliefWeb's API needs an approved `appname`; store it as the `RELIEFWEB_APPNAME` secret. Until then the collector reads the public RSS feed (less metadata).
