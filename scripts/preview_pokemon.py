#!/usr/bin/env python3
"""把「预告世代」的宝可梦（目前是第 10 代御三家）并进 data/pokedex.json。

为什么单独一个脚本：
  主素材源（42arch/pokemon-dataset-zh）只到 No.1025，第 10 代还没进图鉴，
  等它收录还得一阵子。但官方 2026-02-27 已经公布了三只御三家，先放上来看看。

和主管线的关系：
  - fetch_pokemon.py 跑完后会调用这里的 merge_preview()，所以**重跑整条管线不会丢**。
  - 本脚本可以单独跑（幂等），只补预告部分，不动其它 1025 只。

立绘来源：中文神奇宝贝百科（52poke）的官方绘图，转成 site/img/{id}.webp 自托管，
不走外链 —— 她那边 iPhone Safari 打不开 raw.githubusercontent。
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
PREVIEW_JSON = ROOT / "data" / "gen10_preview.json"
POKEDEX_JSON = ROOT / "data" / "pokedex.json"
OUT_IMG = ROOT / "site" / "img"

SIZE = 256
QUALITY = 82
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")


def fetch_art(url: str, dest: pathlib.Path, referer: str = "") -> bool:
    """抓原始 PNG。失败重试 3 次。"""
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if len(data) > 1024:
                dest.write_bytes(data)
                return True
            print(f"[预告] 第 {attempt} 次下载内容过小：{url}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"[预告] 第 {attempt} 次下载失败：{exc}", file=sys.stderr)
    return False


def to_webp(src: pathlib.Path, dest: pathlib.Path) -> bool:
    try:
        with Image.open(src) as im:
            im = im.convert("RGBA")
            im.thumbnail((SIZE, SIZE), Image.LANCZOS)
            im.save(dest, "WEBP", quality=QUALITY, method=3)
        return dest.stat().st_size > 200
    except Exception as exc:  # noqa: BLE001
        print(f"[预告] 压缩失败 {src.name}: {exc}", file=sys.stderr)
        return False


def merge_preview(build_images: bool = True) -> tuple[int, list[str]]:
    """把预告条目并进 pokedex.json。返回 (并入条数, 失败 id 列表)。"""
    if not PREVIEW_JSON.exists():
        print("[预告] 没有 data/gen10_preview.json，跳过")
        return 0, []

    preview = json.loads(PREVIEW_JSON.read_text(encoding="utf-8"))["pokemon"]
    data = json.loads(POKEDEX_JSON.read_text(encoding="utf-8"))
    entries: list[dict] = data.get("pokemon", [])

    by_id = {e["id"]: e for e in entries}
    added = 0
    for p in preview:
        pid = p["id"]
        old = by_id.get(pid)
        if old and old.get("nameZh") == p.get("nameZh") and old.get("preview"):
            # 已经并过且内容一致：只补立绘（下面统一处理），不再重复写
            continue
        entry = dict(p)
        if old:
            # 保留已算好的 art / artShiny
            entry["art"] = old.get("art", "")
            entry["artShiny"] = old.get("artShiny", "")
        entries = [e for e in entries if e["id"] != pid] + [entry]
        by_id[pid] = entry
        added += 1

    entries.sort(key=lambda e: int(e["id"]))
    data["pokemon"] = entries
    data["count"] = len(entries)

    failed: list[str] = []
    if build_images:
        OUT_IMG.mkdir(parents=True, exist_ok=True)
        for p in preview:
            pid = p["id"]
            dest = OUT_IMG / f"{pid}.webp"
            if dest.exists() and dest.stat().st_size > 200:
                continue
            tmp = OUT_IMG / f"_tmp_{pid}.png"
            src = p.get("artSrc")
            if not src:
                failed.append(pid)
                continue
            if not fetch_art(src, tmp, referer="https://wiki.52poke.com/"):
                failed.append(pid)
                tmp.unlink(missing_ok=True)
                continue
            if to_webp(tmp, dest):
                tmp.unlink(missing_ok=True)
                print(f"[预告] {pid} {p.get('nameZh')} 立绘已生成")
            else:
                tmp.unlink(missing_ok=True)
                failed.append(pid)

    for p in preview:
        pid = p["id"]
        for e in entries:
            if e["id"] == pid:
                e["art"] = f"{pid}.webp" if (OUT_IMG / f"{pid}.webp").exists() else ""
                # 官方还没公布闪光形态，不拿普通版冒充
                e["artShiny"] = ""

    POKEDEX_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[预告] 已并入 {added} 条，pokedex.json 共 {len(entries)} 只")
    if failed:
        print(f"[注意] 立绘失败：{', '.join(failed)}")
    return added, failed


def main() -> int:
    added, failed = merge_preview(build_images=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
