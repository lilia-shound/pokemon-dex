"""三个客户端页面的契约断言：投递通道的形状、老办法入口、待发队列。

跑法：

    python3 -B run_tests.py            # 整套
    python3 -B -m tests.test_worker_client
"""

from __future__ import annotations

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

POM_DIR = ROOT / "site" / "pomodoro"
POM_JS = POM_DIR / "app.js"
POM_HTML = POM_DIR / "index.html"


class PomodoroIngestTest(unittest.TestCase):
    """番茄钟页：点同步直接交上去。"""

    def test_the_page_posts_to_the_ingest_endpoint(self):
        js = POM_JS.read_text(encoding="utf-8")
        self.assertIn("postIngest", js, "要有 postIngest")
        self.assertIn("X-Dex-Token", js, "要带 ingest token 头")
        self.assertIn("method: 'POST'", js, "要用 POST")

    def test_the_page_reads_the_ingest_config_from_the_registry(self):
        """ingest 配置来自**已经 fetch 的** sources.json ⇒ 不许新增网络请求。"""
        js = POM_JS.read_text(encoding="utf-8")
        self.assertTrue(re.search(r"d\.ingest|\['ingest'\]", js) is not None,
                        "要从 sources.json 顶层读 ingest 段")
        # 页面本来就有 3 个 fetch（pokedex-lite.json / dex.json / sources.json），
        # 加了投递之后最多 4 个 —— **不许再多来一个注册表请求**
        self.assertLessEqual(len(re.findall(r"fetch\(", js)), 4,
                             "番茄钟页的网络请求不该变多")

    def test_the_page_keeps_the_github_way_as_a_fallback(self):
        """老办法不许删 —— workers.dev 在国内可能抽风。"""
        js = POM_JS.read_text(encoding="utf-8")
        self.assertIn("buildIssueUrl", js, "老办法的 URL 构造要留着")
        self.assertIn("issues/new", js, "老办法的 GitHub 链接要留着")
        self.assertIn("syncOldWay", js, "老办法链接要有固定的 id")
        self.assertIn("ingestAvailable", js, "通道没开时要能退回老办法")

    def test_the_page_queues_failed_reports(self):
        """交不上去要存下来，下次打开自动补发。"""
        js = POM_JS.read_text(encoding="utf-8")
        self.assertIn("pendingIngest", js, "要有待发队列")
        self.assertIn("flushPending", js, "页面打开时要补发")
        self.assertIn("localStorage", js, "待发队列要落 localStorage")


if __name__ == "__main__":
    unittest.main()
