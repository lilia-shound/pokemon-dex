"""源注册表：读 sources.json、校验结构、把各源的原始量折算成额度。

额度是中枢的中间货币：不同行为的单位不可通约（秒 / 分钟 / 个词），
但「每天掉一只宝可梦」的名额是统一的。每个源自己声明「多少原始单位 = 1 额度」。

本模块不碰网络；读文件只在 load_config 一处。
"""

from __future__ import annotations

import json
import os
import pathlib

ROOT_KEYS = ("perAward", "maxPerDay", "maxBackfill", "ledgerDays")
# 每个源必填的字段：enabled 也是必填，手改注册表时拼错键名要当场报错，
# 而不是让这个源静默退出结算。
SOURCE_KEYS = ("name", "unit", "per", "enabled")


class SourceConfigError(RuntimeError):
    """sources.json 结构不对，或读不出来。"""


def _is_int(v) -> bool:
    # bool 是 int 的子类，但 True 不该被当成 1 接受
    return isinstance(v, int) and not isinstance(v, bool)


def validate_config(cfg: dict) -> None:
    """校验注册表结构。任何不合法的形状都抛 SourceConfigError。"""
    if not isinstance(cfg, dict):
        raise SourceConfigError("sources.json 顶层必须是对象")
    for key in ROOT_KEYS:
        if key not in cfg:
            raise SourceConfigError(f"sources.json 缺少 {key}")
        if not _is_int(cfg[key]) or cfg[key] < 1:
            raise SourceConfigError(f"{key} 必须是 >=1 的整数，实际 {cfg[key]!r}")
    sources = cfg.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise SourceConfigError("sources.json 的 sources 必须是非空对象")
    # 微信读书源是结算的必需项：发放器的额度折算与 today.threshold 都按它算。
    # 少了它会算出 threshold=0 并写进对外的 dex.json（图鉴页渲染成「30/0 分」），
    # 而且**没有任何报错**。这条要求放在这里，award.py 与 check_today.py 两个入口自动一致。
    if "weread" not in sources:
        raise SourceConfigError("sources.json 缺少微信读书源（sources.weread）")
    for sid, s in sources.items():
        if not isinstance(s, dict):
            raise SourceConfigError(f"源 {sid} 必须是对象")
        for key in SOURCE_KEYS:
            if key not in s:
                raise SourceConfigError(f"源 {sid} 缺少 {key}")
        # enabled 必须是布尔值：写成 "false" 字符串会被当成真，源照常参与结算；
        # 写成 1 也不该被当成 True。宁可启动就报错，也不要静默改结算口径。
        if not isinstance(s["enabled"], bool):
            raise SourceConfigError(f"源 {sid} 的 enabled 必须是布尔值，实际 {s['enabled']!r}")
        if not _is_int(s["per"]) or s["per"] < 1:
            raise SourceConfigError(f"源 {sid} 的 per 必须是 >=1 的整数，实际 {s['per']!r}")
        if s.get("mode") not in ("pull", "push"):
            raise SourceConfigError(f"源 {sid} 的 mode 必须是 pull 或 push，实际 {s.get('mode')!r}")
        if "maxPerDay" in s and (not _is_int(s["maxPerDay"]) or s["maxPerDay"] < 1):
            raise SourceConfigError(f"源 {sid} 的 maxPerDay 必须是 >=1 的整数")


def load_config(path) -> dict:
    """读 sources.json 并校验。失败一律抛 SourceConfigError（读不到、读不懂、结构不对都算失败）。"""
    p = pathlib.Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SourceConfigError(f"找不到源注册表：{p}") from exc
    except UnicodeDecodeError as exc:
        raise SourceConfigError(f"{p} 不是合法的 UTF-8 文本：{exc}") from exc
    except OSError as exc:
        # 目录路径、权限不足等等：IsADirectoryError 是 OSError 的子类
        raise SourceConfigError(f"读不了源注册表 {p}：{exc}") from exc
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceConfigError(f"{p} 不是合法 JSON：{exc}") from exc
    validate_config(cfg)
    # 旧的环境变量旋钮继续有效：覆盖微信读书的 per
    # （validate_config 已经保证 sources.weread 存在，所以这里不会 KeyError）
    env = os.environ.get("DEX_THRESHOLD")
    if env:
        try:
            value = int(env)
            # 覆盖值也是配置，一样要过校验：per=0 会在折算时 ZeroDivisionError，
            # per<0 会把 900 秒记成负额度。两种都不该拖到运行期才炸。
            if value < 1:
                raise SourceConfigError(f"DEX_THRESHOLD 必须 >= 1，实际 {value!r}")
            cfg["sources"]["weread"]["per"] = value
        except ValueError as exc:
            raise SourceConfigError(f"DEX_THRESHOLD 不是整数：{env!r}") from exc
    return cfg


def enabled_sources(cfg: dict) -> dict:
    """只返回参与结算的源。"""
    # enabled 已被 validate_config 校验过：必填、且必须是布尔值。
    # 这里保持宽松，免得调用方手搓的 dict 少个键就直接 KeyError 崩掉。
    return {sid: s for sid, s in cfg["sources"].items() if s.get("enabled", False)}


def credit_for_day(cfg: dict, ledger_day: dict) -> dict[str, int]:
    """每个启用的源当天折算出多少额度（向下取整，零头不计）。"""
    out: dict[str, int] = {}
    for sid, s in enabled_sources(cfg).items():
        amount = int((ledger_day or {}).get(sid, 0) or 0)
        out[sid] = amount // int(s["per"])
    return out


def day_credit(cfg: dict, ledger_day: dict) -> int:
    """当天全源额度之和。"""
    return sum(credit_for_day(cfg, ledger_day).values())


def credits_for_days(cfg: dict, ledger: dict) -> dict[str, int]:
    """逐日算出额度：{日期: 额度}。"""
    return {day: day_credit(cfg, amounts) for day, amounts in (ledger or {}).items()}
