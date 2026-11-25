"""服务端接收端（scripts/collect_worker.py）的单测。

跑法：

    python3 -B run_tests.py            # 整套
    python3 -B -m tests.test_worker_collect
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import collect_worker as cw
from scripts import sources as src

REGISTRY = ROOT / "data" / "sources.json"


def _cfg():
    return src.load_config(REGISTRY)


class ProcessItemsTest(unittest.TestCase):
    """队列条目 → 账本。"""

    def test_an_accepted_item_merges_into_the_ledger(self):
        cfg = _cfg()
        items = [{"id": "q:1", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 37}}}]
        ledger, results = cw.process_items(items, cfg, "2026-11-25", {})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["action"], "accepted")
        self.assertEqual(results[0]["source"], "pom")
        self.assertEqual(ledger["2026-11-21"]["pom"], 37)

    def test_the_same_day_takes_the_max_not_the_sum(self):
        """同源同一天取 max —— 重复投递是幂等的。"""
        cfg = _cfg()
        items = [
            {"id": "q:1", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 37}}},
            {"id": "q:2", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 20}}},
        ]
        ledger, _ = cw.process_items(items, cfg, "2026-11-25", {})
        self.assertEqual(ledger["2026-11-21"]["pom"], 37)

    def test_an_illegal_item_is_rejected_but_kept_in_the_results(self):
        """非法条目不并账，但**要出现在结果里**（主流程据此把它 ack 掉）。"""
        cfg = _cfg()
        items = [{"id": "q:bad", "body": {"v": 1, "source": "weread", "days": {"2026-11-21": 37}}}]
        ledger, results = cw.process_items(items, cfg, "2026-11-25", {})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["action"], "rejected")
        self.assertEqual(results[0]["id"], "q:bad")
        self.assertTrue(results[0]["reason"], "拒收必须写清原因")
        self.assertEqual(ledger, {})

    def test_a_broken_item_does_not_kill_the_batch(self):
        cfg = _cfg()
        items = [
            {"id": "q:bad", "body": None},
            {"id": "q:ok", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 37}}},
        ]
        ledger, results = cw.process_items(items, cfg, "2026-11-25", {})
        self.assertEqual(len(results), 2)
        self.assertEqual(ledger["2026-11-21"]["pom"], 37)

    def test_the_credit_is_computed_the_same_way_as_the_issue_channel(self):
        """折算出的额度必须与 Issue 通道一致（同一个 day_credit）。"""
        cfg = _cfg()
        items = [{"id": "q:1", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 37}}}]
        _, results = cw.process_items(items, cfg, "2026-11-25", {})
        self.assertEqual(results[0]["credits"], {"2026-11-21": 1})


class MainBehaviourTest(unittest.TestCase):
    """没配置 / 拉不到 ⇒ 跳过，**绝不阻塞结算**。"""

    def test_it_skips_when_the_channel_is_not_configured(self):
        import os
        old = {k: os.environ.pop(k, None) for k in ("WORKER_URL", "WORKER_ADMIN_TOKEN")}
        try:
            self.assertEqual(cw.main([]), 0)
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v

    def test_a_broken_endpoint_is_skipped_not_fatal(self):
        import os
        os.environ["WORKER_URL"] = "http://127.0.0.1:9/not-there"
        os.environ["WORKER_ADMIN_TOKEN"] = "x"
        old_date = os.environ.get("DEX_DATE")
        try:
            # 拉不到 ⇒ 打印警告并 exit 0（不阻塞发奖）
            self.assertEqual(cw.main([]), 0)
        finally:
            os.environ.pop("WORKER_URL", None)
            os.environ.pop("WORKER_ADMIN_TOKEN", None)
            if old_date is not None:
                os.environ["DEX_DATE"] = old_date


# ── 以下是简报之外补的用例 ──────────────────────────────────────────────
# 简报给的 7 条只覆盖了「纯函数」与「拉不到就跳过」，
# 而这条通道真正要紧的两件事——**发出去的请求对不对**、**并完账有没有写盘/ack** ——
# 一条都没测 ⇒ 这里补上（用一台本机的假 Worker，走真的 HTTP）。


class FakeWorker:
    """一台假 Worker：端点与线上一致，只是数据是假的。

      GET  /pull  → {"ok": true, "items": [...]}
      POST /ack   → {"ok": true, "removed": N}

    ★ `removed` 的口径与线上一致：**真删掉的**条数 ——
      键本来就不在队列里的不计数（worker/index.js 里先 get 再 delete）。
    """

    def __init__(self, items=None, admin_token="secret", raw_pull=None):
        self.items = list(items or [])
        self.admin_token = admin_token
        self.raw_pull = raw_pull          # 非 None ⇒ /pull 原样返回它（测坏形状）
        self.acked = []                   # 每次 /ack 收到的 id 列表
        self._httpd = None
        self._thread = None

    def __enter__(self):
        worker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 别把测试输出刷花
                pass

            def _send(self, code, payload):
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _authorized(self):
                return self.headers.get("X-Dex-Admin") == worker.admin_token

            def do_GET(self):
                if self.path.rstrip("/") != "/pull":
                    self._send(404, {"ok": False, "error": "没有这个接口"})
                    return
                if not self._authorized():
                    self._send(401, {"ok": False, "error": "凭证不对"})
                    return
                if worker.raw_pull is not None:
                    self._send(200, worker.raw_pull)
                    return
                self._send(200, {"ok": True, "items": worker.items})

            def do_POST(self):
                if self.path.rstrip("/") != "/ack":
                    self._send(404, {"ok": False, "error": "没有这个接口"})
                    return
                if not self._authorized():
                    self._send(401, {"ok": False, "error": "凭证不对"})
                    return
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    body = {}
                ids = body.get("ids") if isinstance(body, dict) else []
                ids = [i for i in (ids or []) if isinstance(i, str)]
                removed = 0
                for i in ids:
                    # 真的还在队列里 ⇒ 才算删掉了（线上是「先 get 再 delete」）
                    for idx, it in enumerate(worker.items):
                        if isinstance(it, dict) and it.get("id") == i:
                            del worker.items[idx]
                            removed += 1
                            break
                worker.acked.append(ids)
                self._send(200, {"ok": True, "removed": removed})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        return False

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self._httpd.server_address[1]


class WorkerEndpointTest(unittest.TestCase):
    """对着真的 HTTP 说话 —— 验路径、请求头、payload。"""

    def test_pull_returns_the_items(self):
        items = [{"id": "q:1", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 37}},
                  "receivedAt": "2026-11-21T01:02:03.000Z"}]
        with FakeWorker(items) as w:
            got = cw.pull_items(w.url, "secret")
        self.assertEqual(got, items)

    def test_pull_raises_when_the_admin_token_is_wrong(self):
        """凭证不对 ⇒ 抛异常（由 main 兜住并 exit 0）。"""
        with FakeWorker() as w:
            with self.assertRaises(RuntimeError):
                cw.pull_items(w.url, "wrong-token")

    def test_pull_raises_when_the_shape_is_not_expected(self):
        with FakeWorker(raw_pull={"ok": True, "items": "不是列表"}) as w:
            with self.assertRaises(RuntimeError):
                cw.pull_items(w.url, "secret")

    def test_ack_returns_the_number_actually_removed(self):
        """返回**真删掉**的条数，不是「我们请求删了几条」。"""
        with FakeWorker([{"id": "q:1"}, {"id": "q:2"}]) as w:
            n = cw.ack_items(w.url, "secret", ["q:1", "q:2", "q:gone"])
        self.assertEqual(n, 2)          # q:gone 本来就不在队列里
        self.assertEqual(w.acked, [["q:1", "q:2", "q:gone"]])
        self.assertEqual(w.items, [])

    def test_ack_with_nothing_to_ack_does_not_call_the_worker(self):
        with FakeWorker() as w:
            self.assertEqual(cw.ack_items(w.url, "secret", []), 0)
            self.assertEqual(cw.ack_items(w.url, "secret", [None, ""]), 0)
        self.assertEqual(w.acked, [])

    def test_ack_raises_when_the_admin_token_is_wrong(self):
        with FakeWorker() as w:
            with self.assertRaises(RuntimeError):
                cw.ack_items(w.url, "wrong-token", ["q:1"])


class MainEndToEndTest(unittest.TestCase):
    """main() 走完整条路：拉 → 并账本 → 写盘 → ack。

    ★ **DEX_PATH 指到临时文件** —— 真数据（data/dex.json）不许被测试改到。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dex = pathlib.Path(self.tmp.name) / "dex.json"
        self.dex.write_text(
            json.dumps({"version": 2, "collected": [], "ledger": {}}, ensure_ascii=False),
            encoding="utf-8")
        # DEX_PATH 是模块全局量 —— 用完必须还原
        orig = cw.DEX_PATH
        self.addCleanup(setattr, cw, "DEX_PATH", orig)
        cw.DEX_PATH = self.dex

    def _set_env(self, key, value):
        old = os.environ.get(key)

        def restore():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

        self.addCleanup(restore)
        os.environ[key] = value

    def _read(self):
        return json.loads(self.dex.read_text(encoding="utf-8"))

    def _run_main(self, argv):
        """跑 main()，把它的输出收起来（别把测试报告刷花）。"""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cw.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_it_merges_writes_the_ledger_and_acks(self):
        items = [{"id": "q:1", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 37}}}]
        with FakeWorker(items) as w:
            self._set_env("WORKER_URL", w.url)
            self._set_env("WORKER_ADMIN_TOKEN", "secret")
            rc, _out, _err = self._run_main(["--date", "2026-11-25"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._read()["ledger"], {"2026-11-21": {"pom": 37}})
        self.assertEqual(w.acked, [["q:1"]])
        self.assertEqual(w.items, [])          # 队列里那条真的被删掉了

    def test_a_rejected_item_is_acked_too_and_the_ledger_stays_put(self):
        """非法条目也要 ack 掉（否则每轮都拉同一批垃圾），但**不许并账**。"""
        items = [{"id": "q:bad", "body": {"v": 1, "source": "weread", "days": {"2026-11-21": 37}}}]
        with FakeWorker(items) as w:
            self._set_env("WORKER_URL", w.url)
            self._set_env("WORKER_ADMIN_TOKEN", "secret")
            rc, _out, _err = self._run_main(["--date", "2026-11-25"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._read()["ledger"], {})
        self.assertEqual(w.acked, [["q:bad"]])

    def test_dry_run_writes_nothing_and_acks_nothing(self):
        items = [{"id": "q:1", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 37}}}]
        with FakeWorker(items) as w:
            self._set_env("WORKER_URL", w.url)
            self._set_env("WORKER_ADMIN_TOKEN", "secret")
            rc, _out, _err = self._run_main(["--dry-run", "--date", "2026-11-25"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._read()["ledger"], {})
        self.assertEqual(w.acked, [])
        self.assertEqual(len(w.items), 1)      # 条目还在队列里

    def test_an_empty_queue_changes_nothing(self):
        with FakeWorker() as w:
            self._set_env("WORKER_URL", w.url)
            self._set_env("WORKER_ADMIN_TOKEN", "secret")
            rc, _out, _err = self._run_main(["--date", "2026-11-25"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._read()["ledger"], {})
        self.assertEqual(w.acked, [])

    def test_the_ledger_takes_the_max_when_the_same_day_is_delivered_twice(self):
        """重复投递是幂等的 —— 账本取 max，不叠加。"""
        items = [
            {"id": "q:1", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 37}}},
            {"id": "q:2", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 20}}},
        ]
        with FakeWorker(items) as w:
            self._set_env("WORKER_URL", w.url)
            self._set_env("WORKER_ADMIN_TOKEN", "secret")
            rc, _out, _err = self._run_main(["--date", "2026-11-25"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._read()["ledger"], {"2026-11-21": {"pom": 37}})


class AmountShapeTest(unittest.TestCase):
    """数值形状与 Worker 侧对齐（两边结论不一致最伤）。"""

    def test_an_integer_valued_float_is_accepted(self):
        """整数值浮点当整数收 —— Worker 侧 Number.isInteger(1440.0) 是 true，
        它会收下这份报告 ⇒ 这边也得收，否则「Worker 收下、服务端丢掉」。"""
        items = [{"id": "q:1", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 1440.0}}}]
        ledger, results = cw.process_items(items, _cfg(), "2026-11-25", {})
        self.assertEqual(results[0]["action"], "accepted")
        self.assertEqual(ledger["2026-11-21"]["pom"], 1440)

    def test_a_fractional_amount_is_rejected(self):
        items = [{"id": "q:1", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 37.5}}}]
        ledger, results = cw.process_items(items, _cfg(), "2026-11-25", {})
        self.assertEqual(results[0]["action"], "rejected")
        self.assertEqual(ledger, {})

    def test_a_boolean_amount_is_rejected(self):
        """布尔不是整数（Python 里 bool 是 int 的子类）。"""
        items = [{"id": "q:1", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": True}}}]
        ledger, results = cw.process_items(items, _cfg(), "2026-11-25", {})
        self.assertEqual(results[0]["action"], "rejected")
        self.assertEqual(ledger, {})

    def test_the_original_ledger_is_not_mutated(self):
        ledger = {"2026-11-21": {"weread": 900}}
        items = [{"id": "q:1", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 37}}}]
        new_ledger, _ = cw.process_items(items, _cfg(), "2026-11-25", ledger)
        self.assertEqual(ledger, {"2026-11-21": {"weread": 900}})
        self.assertEqual(new_ledger["2026-11-21"], {"weread": 900, "pom": 37})


class BrowserUserAgentTest(unittest.TestCase):
    """★ 请求必须带浏览器 UA —— 否则 Cloudflare 会 403（error code 1010）。

    ★ 这不是假设：**2026-11-25 真机上撞到了**。
      urllib 的默认 UA（`Python-urllib/3.11`）会被 Cloudflare 按「浏览器签名」封掉
      ⇒ `HTTP 403 error code: 1010` ⇒ cron 每轮都拉不到队列。
      （与 scripts/notify.py 给 Resend 加 UA 是同一件事 —— Resend 也在 Cloudflare 后面。）
    """

    def test_the_request_carries_a_browser_user_agent(self):
        """对着真的 HTTP 说话 ⇒ 看服务端收到的 User-Agent 是什么。"""
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen["ua"] = self.headers.get("User-Agent")
                raw = json.dumps({"ok": True, "items": []}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            url = "http://127.0.0.1:%d" % httpd.server_address[1]
            status, _data = cw._http(url + "/pull", "GET",
                                     headers={"X-Dex-Admin": "secret"})
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
        self.assertEqual(status, 200)
        ua = seen.get("ua") or ""
        self.assertNotIn("Python-urllib", ua,
                         "还带着 urllib 的默认 UA ⇒ Cloudflare 会 403（1010）")
        self.assertIn("Mozilla/5.0", ua, "UA 应当像个浏览器")

    def test_the_caller_can_still_override_the_user_agent(self):
        """调用方自己给了 UA ⇒ 尊重它（别覆盖掉）。"""
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen["ua"] = self.headers.get("User-Agent")
                raw = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            url = "http://127.0.0.1:%d" % httpd.server_address[1]
            cw._http(url + "/pull", "GET", headers={"User-Agent": "pokemon-dex/1"})
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
        self.assertEqual(seen.get("ua"), "pokemon-dex/1")


class PullRetryTest(unittest.TestCase):
    """★ 拉队列要重试一次 —— 这条链路在国内会抖。

    ★ 实测：TLS 被掐、冷启动 4~15 秒 ⇒ 一次失败就放弃 ⇒
      这一轮的报告要等 3 小时后的下一轮才被拉走 ⇒ 她会以为「交了没动静」。
    """

    def setUp(self):
        self._orig_pull = cw.pull_items
        self._orig_sleep = cw.time.sleep
        cw.time.sleep = lambda _s: None      # 别真的等

    def tearDown(self):
        cw.pull_items = self._orig_pull
        cw.time.sleep = self._orig_sleep

    def test_a_transient_failure_is_retried(self):
        calls = []

        def flaky(url, admin_token, timeout=0):
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError("拉队列失败：HTTP 0 读超时")
            return [{"id": "q:1"}]

        cw.pull_items = flaky
        items = cw._pull_with_retry("https://x.example", "tok")
        self.assertEqual(items, [{"id": "q:1"}])
        self.assertEqual(len(calls), 2, "第一次失败后应该再试一次")

    def test_it_gives_up_after_the_attempts_are_used_up(self):
        calls = []

        def always_fail(url, admin_token, timeout=0):
            calls.append(url)
            raise RuntimeError("拉队列失败：HTTP 0 读超时")

        cw.pull_items = always_fail
        with self.assertRaises(RuntimeError):
            cw._pull_with_retry("https://x.example", "tok")
        self.assertEqual(len(calls), cw.PULL_ATTEMPTS,
                         "重试次数应当是 PULL_ATTEMPTS（不是无限重试）")

    def test_a_wrong_token_is_not_retried(self):
        """401 重试也没用 ⇒ 不该浪费一轮。"""
        calls = []

        def unauthorized(url, admin_token, timeout=0):
            calls.append(url)
            raise RuntimeError("拉队列失败：HTTP 401 凭证不对")

        cw.pull_items = unauthorized
        with self.assertRaises(RuntimeError):
            cw._pull_with_retry("https://x.example", "tok")
        self.assertEqual(len(calls), 1, "凭证不对时不该重试")


class AckRetryTest(unittest.TestCase):
    """★ ack 也要重试 —— 这条链路会抖。

    ★ ack 失败的后果不严重（条目还在队列里 ⇒ 下轮重拉 ⇒ 并账取 max ⇒ 不会多算），
      但会让队列**一直有残留** ⇒ 每轮都白拉一遍。
      ack 天然幂等 ⇒ 重试安全。
    """

    def setUp(self):
        self._orig_ack = cw.ack_items
        self._orig_sleep = cw.time.sleep
        cw.time.sleep = lambda _s: None

    def tearDown(self):
        cw.ack_items = self._orig_ack
        cw.time.sleep = self._orig_sleep

    def test_a_transient_failure_is_retried(self):
        calls = []

        def flaky(url, admin_token, ids, timeout=0):
            calls.append(list(ids))
            if len(calls) == 1:
                raise RuntimeError("ack 失败：HTTP 0 TLS EOF")
            return len(ids)

        cw.ack_items = flaky
        n = cw._ack_with_retry("https://x.example", "tok", ["q:1", "q:2"])
        self.assertEqual(n, 2)
        self.assertEqual(len(calls), 2, "第一次失败后应该再试一次")
        self.assertEqual(calls[0], ["q:1", "q:2"], "重试时交的还是同一批 id")

    def test_it_gives_up_after_the_attempts_are_used_up(self):
        calls = []

        def always_fail(url, admin_token, ids, timeout=0):
            calls.append(list(ids))
            raise RuntimeError("ack 失败：HTTP 0 TLS EOF")

        cw.ack_items = always_fail
        with self.assertRaises(RuntimeError):
            cw._ack_with_retry("https://x.example", "tok", ["q:1"])
        self.assertEqual(len(calls), cw.ACK_ATTEMPTS)

    def test_a_wrong_token_is_not_retried(self):
        calls = []

        def unauthorized(url, admin_token, ids, timeout=0):
            calls.append(list(ids))
            raise RuntimeError("ack 失败：HTTP 401 凭证不对")

        cw.ack_items = unauthorized
        with self.assertRaises(RuntimeError):
            cw._ack_with_retry("https://x.example", "tok", ["q:1"])
        self.assertEqual(len(calls), 1, "凭证不对时不该重试")


if __name__ == "__main__":
    unittest.main()
