#!/usr/bin/env python3
"""宝可梦素材管线：42arch/pokemon-dataset-zh -> data/pokedex.json + data/images/home/

数据源：
  - 42arch/pokemon-dataset-zh（MIT，抓取自神奇宝贝百科）—— 中文名/图鉴文案/属性/进化链
  - PokeAPI —— 少数没有 HOME 造型的宝可梦的官方立绘与中文图鉴文案兜底

素材本身版权归 Nintendo / Creatures Inc. / GAME FREAK inc.，仅供个人自用。
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import time
import urllib.request

REPO = "https://github.com/42arch/pokemon-dataset-zh.git"
ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / ".cache" / "pokemon-dataset-zh"
OUT_JSON = ROOT / "data" / "pokedex.json"
OUT_IMG = ROOT / "data" / "images" / "home"

# 文件名形如 "0025-皮卡丘" / "0025-皮卡丘-雄性" / "0906-新叶喵-世界帽子"
_STEM_RE = re.compile(r"^(\d+)-(.*)$")
# 这些后缀代表形态变体，图鉴主图不要用
_FORM_MARKERS = ("极巨化", "帽子", "形态", "特殊")


def _tier(stem: str, name_zh: str) -> int:
    """立绘优先级：0=标准形态 1=性别变体 2=其它变体 3=形态/特殊（尽量不用）。"""
    m = _STEM_RE.match(stem)
    if not m:
        return 3
    rest = m.group(2)
    if rest == name_zh:
        return 0
    if rest.startswith(name_zh + "-"):
        tail = rest[len(name_zh) + 1:]
        if tail in ("雄性", "雌性"):
            return 1
        if any(k in tail for k in _FORM_MARKERS):
            return 3
        return 2
    return 3


def pick_art(home_images: list, name_zh: str, want_shiny: bool) -> str | None:
    """从 home_images 里挑主立绘。

    优先标准形态，其次性别变体，再次其它变体；形态/超极巨化/帽子类最后。
    没有闪光版就返回 None（不拿普通版冒充闪光）。
    """
    key = "shiny" if want_shiny else "image"
    entries = []
    for item in home_images or []:
        fname = item.get(key)
        if not fname:
            continue
        stem = fname[:-4] if fname.endswith(".png") else fname
        if stem.endswith("-shiny"):
            stem = stem[: -len("-shiny")]
        entries.append((_tier(stem, name_zh), len(entries), fname))
    if not entries:
        return None
    entries.sort()
    return entries[0][2]


def _first_flavor(src: dict) -> str:
    """取一条官方中文图鉴描述（各世代各版本里第一条非空的）。"""
    for gen in src.get("pokedex_entries") or []:
        for ver in gen.get("versions") or []:
            text = (ver.get("text") or "").strip()
            if text:
                return text
    return ""


def _fallback_name(pid: str) -> str:
    return f"fallback-{pid}.png"


def _fetch_fallback_art(pid: str) -> bool:
    """HOME 造型缺失的少数宝可梦，用 PokeAPI 官方立绘兜底。

    走 jsdelivr 代理（沙箱可达）；文件名统一为 fallback-{编号}.png。
    """
    url = (f"https://cdn.jsdelivr.net/gh/PokeAPI/sprites@master/sprites/pokemon/"
           f"other/official-artwork/{int(pid)}.png")
    dest = OUT_IMG / _fallback_name(pid)
    if dest.exists() and dest.stat().st_size > 1024:
        return True
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=40) as resp:
                data = resp.read()
            if len(data) > 1024:
                dest.write_bytes(data)
                return True
        except Exception as exc:  # noqa: BLE001
            print(f"[管线] 兜底立绘 {pid} 第 {attempt + 1} 次失败：{exc}")
            time.sleep(2)
    return False


def _fetch_pokeapi_flavor(pid: str) -> str:
    """PokeAPI 的简体中文官方图鉴文案（zh-hans）。没有就返回空串。"""
    url = f"https://pokeapi.co/api/v2/pokemon-species/{int(pid)}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=40) as resp:
                data = json.load(resp)
            entries = data.get("flavor_text_entries") or []
            zh = [f for f in entries if (f.get("language") or {}).get("name") == "zh-hans"]
            if not zh:
                return ""
            text = (zh[0].get("flavor_text") or "").replace("\n", " ").replace("\f", " ").strip()
            return text
        except Exception as exc:  # noqa: BLE001
            print(f"[管线] PokeAPI 图鉴 {pid} 第 {attempt + 1} 次失败：{exc}")
            time.sleep(2)
    return ""


def extract(src: dict) -> dict:
    """把 42arch 单只 JSON 压成图鉴页需要的精简字段。"""
    pid = str(src.get("pokedex_id") or "").zfill(4)
    name_zh = src.get("name_zh") or ""
    forms = src.get("forms") or []
    form = forms[0] if forms else {}
    abilities = [a.get("name") for a in (form.get("abilities") or []) if a.get("name")]
    chain = []
    for link in src.get("evolution_chains") or []:
        for node in link:
            if node.get("name"):
                chain.append(node["name"])
    art = pick_art(src.get("home_images") or [], name_zh, False)
    art_shiny = pick_art(src.get("home_images") or [], name_zh, True)
    flavor = _first_flavor(src)
    return {
        "id": pid,
        "nameZh": name_zh,
        "nameJa": src.get("name_ja") or "",
        "nameEn": src.get("name_en") or "",
        "types": form.get("types") or [],
        "category": form.get("category") or "",
        "abilities": abilities,
        "height": form.get("height") or "",
        "weight": form.get("weight") or "",
        "flavor": flavor,
        "flavorSource": "dex" if flavor else "",
        "summary": (src.get("description") or "").strip(),
        "profile": (src.get("profile") or "").strip(),
        "evolution": chain,
        "art": art or _fallback_name(pid),
        "artShiny": art_shiny,
        "artSource": "home" if art else "pokeapi-official",
    }


def _clone() -> pathlib.Path:
    """拉取素材仓库。网络不稳时重试，已完整则直接复用。"""
    if (SRC_DIR / "data" / "pokemon").is_dir() and (SRC_DIR / "data" / "images" / "home").is_dir():
        print("[管线] 复用已有素材缓存")
        return SRC_DIR

    last_err = None
    for attempt in range(1, 4):
        if SRC_DIR.exists():
            shutil.rmtree(SRC_DIR, ignore_errors=True)
        try:
            print(f"[管线] sparse clone 素材仓库（第 {attempt}/3 次）…")
            subprocess.run(
                ["git", "clone", "--filter=blob:none", "--no-checkout", "--depth", "1", REPO, str(SRC_DIR)],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(SRC_DIR), "sparse-checkout", "set", "data/pokemon", "data/images/home"],
                check=True,
            )
            subprocess.run(["git", "-C", str(SRC_DIR), "checkout"], check=True)
            if (SRC_DIR / "data" / "pokemon").is_dir():
                return SRC_DIR
            last_err = RuntimeError("clone 成功但没拿到 data/pokemon")
        except subprocess.CalledProcessError as exc:
            last_err = exc
            print(f"[管线] 第 {attempt} 次失败：{exc}")
            time.sleep(3 * attempt)
    raise RuntimeError(f"素材仓库拉取失败：{last_err}")


def build() -> int:
    src_dir = _clone()
    files = sorted((src_dir / "data" / "pokemon").glob("*.json"))
    if not files:
        print("[失败] 没拿到任何宝可梦数据", file=sys.stderr)
        return 1

    entries = []
    for f in files:
        src = json.loads(f.read_text(encoding="utf-8"))
        entry = extract(src)
        if entry["nameZh"]:
            entries.append(entry)

    # 图片：先整目录同步（不整目录删，避免 NAS 上 rmtree 竞态）
    src_img = src_dir / "data" / "images" / "home"
    OUT_IMG.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_img, OUT_IMG, dirs_exist_ok=True)

    # 缺 HOME 立绘的，用 PokeAPI 官方立绘兜底
    for e in entries:
        if e["artSource"] != "home":
            ok = _fetch_fallback_art(e["id"])
            print(f"[管线] 兜底立绘 {e['id']} {e['nameZh']}: {'ok' if ok else '失败'}")

    # 缺中文图鉴文案的，依次尝试 PokeAPI -> 外观描述
    for e in entries:
        if not e["flavor"]:
            zh = _fetch_pokeapi_flavor(e["id"])
            if zh:
                e["flavor"] = zh
                e["flavorSource"] = "pokeapi"
                print(f"[管线] PokeAPI 补图鉴 {e['id']} {e['nameZh']}")
    still_empty = []
    for e in entries:
        if not e["flavor"]:
            e["flavor"] = (e.get("profile") or "").split("\n")[0].strip()
            e["flavorSource"] = "profile" if e["flavor"] else ""
            if not e["flavor"]:
                still_empty.append(e["id"])

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps({"count": len(entries), "pokemon": entries}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    print(f"[完成] {len(entries)} 只 -> {OUT_JSON.relative_to(ROOT)}")
    print(f"[完成] 图片 {sum(1 for _ in OUT_IMG.glob('*.png'))} 张 -> {OUT_IMG.relative_to(ROOT)}")
    if still_empty:
        print(f"[注意] {len(still_empty)} 只既无图鉴文案也无外观描述: {', '.join(still_empty)}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(build())
