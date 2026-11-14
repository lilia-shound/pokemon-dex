#!/usr/bin/env python3
"""把宝可梦立绘压成 WebP 并放进 site/img/。

为什么要有这一步：
  原本立绘走 raw.githubusercontent.com 直链，不落盘 —— 但那个域名**在国内基本访问不了**，
  姜来在 iPhone Safari 上打开图鉴页时整页图片全裂。
  所以图片必须自托管，走 github.io（她那边实测能正常打开页面）。

同时：
  - 512px PNG → 256px WebP q82，体积从 ~30MB 压到 ~15MB，git push 不容易断
  - 文件名改成纯 ASCII（0563.webp / 0563-shiny.webp），避开中文 URL 编码风险
  - 12 只没有 HOME 造型的，从 PokeAPI 官方立绘补
"""

from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "pokemon-dataset-zh" / "data" / "images" / "home"
OUT = ROOT / "site" / "img"
POKEDEX = ROOT / "data" / "pokedex.json"
POKEAPI_ART = ("https://cdn.jsdelivr.net/gh/PokeAPI/sprites@master/sprites/pokemon/"
                "other/official-artwork/{}.png")

SIZE = 256
QUALITY = 82


def find_source(fname: str) -> pathlib.Path | None:
    """在本地缓存里找原始 PNG（文件名含中文，按后缀匹配）。"""
    if not fname:
        return None
    stem = fname[:-4] if fname.endswith(".png") else fname
    if stem.endswith("-shiny"):
        stem = stem[: -len("-shiny")]
    cands = sorted(CACHE.glob(f"*-{stem}.png")) + sorted(CACHE.glob(f"{stem}.png"))
    return cands[0] if cands else None


def fetch_pokeapi(pid: str, dest: pathlib.Path) -> bool:
    url = POKEAPI_ART.format(int(pid))
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as resp:
                data = resp.read()
            if len(data) > 1024:
                dest.write_bytes(data)
                return True
        except Exception as exc:  # noqa: BLE001
            print(f"[管线] PokeAPI {pid} 第 {attempt + 1} 次失败：{exc}", file=sys.stderr)
    return False


def to_webp(src: pathlib.Path, dest: pathlib.Path) -> bool:
    try:
        with Image.open(src) as im:
            im = im.convert("RGBA")
            im.thumbnail((SIZE, SIZE), Image.LANCZOS)
            im.save(dest, "WEBP", quality=QUALITY, method=3)
        return dest.stat().st_size > 200
    except Exception as exc:  # noqa: BLE001
        print(f"[管线] 压缩失败 {src.name}: {exc}", file=sys.stderr)
        return False


def main() -> int:
    data = json.loads(POKEDEX.read_text(encoding="utf-8"))
    entries = data["pokemon"]
    OUT.mkdir(parents=True, exist_ok=True)

    # 先按当前 art / artShiny 的**文件名**建索引（取 URL 最后一段）
    def fname_of(url: str) -> str:
        from urllib.parse import unquote
        return unquote(url.rsplit("/", 1)[-1]) if url else ""

    ok = fail = 0
    for e in entries:
        pid = e["id"]
        normal_src = find_source(fname_of(e.get("art", "")))
        if normal_src is None:
            # 12 只没有 HOME 造型，从 PokeAPI 拿原图再压
            tmp = OUT / f"_tmp_{pid}.png"
            if fetch_pokeapi(pid, tmp):
                normal_src = tmp
        if normal_src is None:
            print(f"[管线] 找不到 {pid} {e['nameZh']} 的立绘")
            fail += 1
            e["art"] = ""
            continue
        dest = OUT / f"{pid}.webp"
        if not to_webp(normal_src, dest):
            fail += 1
            e["art"] = ""
        else:
            e["art"] = f"{pid}.webp"
            ok += 1
        if normal_src.name.startswith("_tmp_"):
            normal_src.unlink(missing_ok=True)

        shiny_src = find_source(fname_of(e.get("artShiny", "")))
        if shiny_src is not None:
            sdest = OUT / f"{pid}-shiny.webp"
            if to_webp(shiny_src, sdest):
                e["artShiny"] = f"{pid}-shiny.webp"
            else:
                e["artShiny"] = ""
        else:
            e["artShiny"] = ""

    POKEDEX.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    total = sum(1 for _ in OUT.glob("*.webp"))
    size_mb = sum(f.stat().st_size for f in OUT.glob("*.webp")) / 1024 / 1024
    print(f"[完成] 主立绘 {ok} 只（失败 {fail}）· 共 {total} 张 WebP · {size_mb:.1f} MB")
    missing = [e["id"] for e in entries if not e["art"]]
    if missing:
        print(f"[注意] {len(missing)} 只仍无图: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
