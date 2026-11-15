"""公開用の静的サイトを1つのフォルダに組み立てる: site/ + data/ + manifest.json → _site/

    python scripts/build_site.py                 # _site/ を作る
    python -m http.server -d _site 8000          # http://localhost:8000 で確認

GitHub Pages 上ではブラウザがフォルダの中身を一覧できないため、
どの月の出来事ファイル・どの年の指標ファイルがあるかを manifest.json に書き出して、
画面(site/app.js)がそれを見て必要なファイルだけを読み込めるようにする。
収集基盤と同じく、Python の標準ライブラリだけで動く(追加のインストール不要)。
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

# リポジトリの一番上のフォルダ(このファイルは scripts/ の中にある)
ROOT = Path(__file__).resolve().parent.parent


def build_manifest(data_dir: Path) -> dict:
    """data/ の中を調べて、ソースごとに存在するファイル名(拡張子なし)の一覧を作る。

    例: {"events": {"usgs": ["2026-10", "2026-11"]}, "indicators": {"unhcr": ["2025"]}}
    名前は昇順に並べる。画面側は「最後の要素 = 最新」として扱うため、この順番は崩さないこと。
    data/analysis/ など events・indicators 以外のフォルダは、画面がまだ使わないので載せない。
    """
    manifest = {
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "events": {},
        "indicators": {},
    }
    for kind in ("events", "indicators"):
        base = data_dir / kind
        if not base.is_dir():
            # まだ一度も収集していない種類は飛ばす(空の一覧になる)
            continue
        for src in sorted(p for p in base.iterdir() if p.is_dir()):
            names = sorted(p.stem for p in src.glob("*.json"))
            # ファイルが1つもないソースは載せない(画面で空のソースを扱わずに済むように)
            if names:
                manifest[kind][src.name] = names
    return manifest


def build(site_dir: Path, data_dir: Path, out_dir: Path) -> dict:
    """site_dir と data_dir をコピーして out_dir に公開用のサイトを作り、manifest を返す。

    out_dir は毎回いったん消してから作り直す(前回の古いファイルが残って公開されないように)。
    """
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(site_dir, out_dir)
    out_data = out_dir / "data"
    # .gitkeep(空フォルダを Git に残すための目印)は公開不要なので除く
    if data_dir.is_dir():
        shutil.copytree(data_dir, out_data, ignore=shutil.ignore_patterns(".gitkeep"))
    else:
        out_data.mkdir()
    manifest = build_manifest(data_dir)
    (out_data / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    # GitHub Pages の Jekyll 変換を止め、ファイルをそのまま配信させる(_ で始まるファイルなどが消されるのを防ぐ)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return manifest


def main(argv=None) -> None:
    """コマンドラインの入口。フォルダの場所は引数で変えられる(既定はリポジトリ内の site/, data/, _site/)。"""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site", type=Path, default=ROOT / "site")
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=ROOT / "_site")
    args = ap.parse_args(argv)
    manifest = build(args.site, args.data, args.out)
    n = sum(len(v) for v in manifest["events"].values())
    print(f"built {args.out} ({n} event files, sources: {', '.join(manifest['events'])})")


if __name__ == "__main__":
    main()
