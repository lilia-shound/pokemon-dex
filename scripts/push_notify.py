"""微信推送（pushplus）。未配置 token 时静默跳过。"""

from __future__ import annotations

import json
import os
import urllib.request


def send(title: str, content: str) -> bool:
    token = os.environ.get("PUSHPLUS_TOKEN")
    if not token:
        print("[推送] 未配置 PUSHPLUS_TOKEN，跳过")
        return False
    req = urllib.request.Request(
        "https://www.pushplus.plus/send",
        data=json.dumps({"token": token, "title": title, "content": content}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        print(f"[推送] 失败: {exc}")
        return False
    ok = payload.get("code") == 200
    print(f"[推送] {'成功' if ok else '失败'}: {payload.get('msg')}")
    return ok
