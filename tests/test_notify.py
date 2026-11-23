"""邮件通知（`scripts/notify.py`）的测试。

重点盯三件事：

1. **补发的宝可梦也要通知** —— `award.py` 会一次结清所有待发奖日，
   所以「今天」这轮可能收服的是前天甚至大前天那只 ⇒ 判定不能只看 date == 今天。
2. **幂等** —— 同一条目（同一天同一只）永远只通知一次。
3. **连读提醒的四个条件** —— streak≥2、今天没达标、≥21:00、今天没发过。

跑法：

    python3 -B run_tests.py                          # 整套
    python3 -B -m tests.test_notify                 # 只跑这个文件
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import tempfile
import unittest

from scripts import notify


class AwardDetectTest(unittest.TestCase):
    """判定「哪些条目还没通知过」。"""

    def test_key_is_date_and_id(self):
        self.assertEqual(notify.award_key({"date": "2026-11-22", "id": "0163"}),
                         "2026-11-22#0163")

    def test_a_fresh_entry_is_pending(self):
        got = notify.pending_awards([{"date": "2026-11-22", "id": "0163"}], [])
        self.assertEqual(len(got), 1)

    def test_an_already_notified_entry_is_not_pending(self):
        entries = [{"date": "2026-11-22", "id": "0163"}]
        got = notify.pending_awards(entries, ["2026-11-22#0163"])
        self.assertEqual(got, [])

    def test_a_backfilled_older_day_is_still_pending(self):
        """★ 补发的也要通知 —— 不能按「是不是今天」筛。"""
        entries = [{"date": "2026-11-20", "id": "0471"}]
        got = notify.pending_awards(entries, [])
        self.assertEqual([e["id"] for e in got], ["0471"])

    def test_the_same_id_on_two_days_counts_twice(self):
        entries = [{"date": "2026-11-20", "id": "0471"},
                   {"date": "2026-11-22", "id": "0471"}]
        got = notify.pending_awards(entries, ["2026-11-20#0471"])
        self.assertEqual([e["date"] for e in got], ["2026-11-22"])

    def test_empty_input_gives_empty_output(self):
        self.assertEqual(notify.pending_awards([], []), [])


class StreakWarnTest(unittest.TestCase):
    """判定「连读快断了」。"""

    def _now(self, hour):
        return datetime.datetime(2026, 11, 23, hour, 0, 0)

    def test_warns_when_streak_is_two_and_not_reached(self):
        self.assertTrue(notify.should_warn_streak(2, 1, 2, self._now(21), False))

    def test_does_not_warn_when_streak_is_one(self):
        """连读只有 1 天时断了无所谓 —— 不打扰。"""
        self.assertFalse(notify.should_warn_streak(1, 0, 2, self._now(21), False))

    def test_does_not_warn_before_nine_pm(self):
        self.assertFalse(notify.should_warn_streak(4, 0, 2, self._now(20), False))

    def test_does_not_warn_after_reaching_the_goal(self):
        self.assertFalse(notify.should_warn_streak(4, 2, 2, self._now(21), False))

    def test_does_not_warn_twice_in_one_day(self):
        self.assertFalse(notify.should_warn_streak(4, 0, 2, self._now(22), True))

    def test_warns_at_exactly_nine_pm(self):
        self.assertTrue(notify.should_warn_streak(4, 0, 2, self._now(21), False))


class StateFileTest(unittest.TestCase):
    """幂等标记存在哪儿。"""

    def test_missing_file_gives_an_empty_state(self):
        with tempfile.TemporaryDirectory() as d:
            st = notify.load_state(os.path.join(d, "nope.json"))
            self.assertEqual(st, {"awarded": [], "streak": {}})

    def test_broken_file_gives_an_empty_state(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            pathlib.Path(p).write_text("{ not json", encoding="utf-8")
            st = notify.load_state(p)
            self.assertEqual(st["awarded"], [])

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            notify.save_state({"awarded": ["a#b"], "streak": {"2026-11-23": True}}, p)
            st = notify.load_state(p)
            self.assertEqual(st["awarded"], ["a#b"])
            self.assertTrue(st["streak"]["2026-11-23"])

    def test_state_file_is_written_as_utf8_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            notify.save_state({"awarded": [], "streak": {"2026-11-23": True}}, p)
            raw = pathlib.Path(p).read_text(encoding="utf-8")
            self.assertIn("2026-11-23", raw)
            self.assertEqual(json.loads(raw)["streak"]["2026-11-23"], True)


class EmailContentTest(unittest.TestCase):
    """邮件正文：要有立绘、名字、闪光标、连读天数、连读提醒的安慰话。"""

    POKEDEX = {
        "0961": {"id": "0961", "nameZh": "三海地鼠", "types": ["水", "地面"],
                 "art": "0961.webp", "artShiny": "0961-shiny.webp"},
    }

    def test_award_email_names_the_pokemon(self):
        subject, html = notify.build_award_email(
            [{"date": "2026-11-23", "id": "0961", "nameZh": "三海地鼠", "shiny": False}],
            self.POKEDEX, streak=4, total=9, source_text="微信读书 30 分钟 + 番茄钟 25 分钟")
        self.assertIn("三海地鼠", subject)
        self.assertIn("三海地鼠", html)
        self.assertIn("0961.webp", html)

    def test_award_email_marks_shiny(self):
        _, html = notify.build_award_email(
            [{"date": "2026-11-23", "id": "0961", "nameZh": "三海地鼠", "shiny": True}],
            self.POKEDEX, streak=4, total=9, source_text="微信读书 30 分钟")
        self.assertIn("闪光", html)
        self.assertIn("0961-shiny.webp", html)

    def test_award_email_shows_the_award_day(self):
        """补发的要标出是哪天的 —— 不能让人以为是今天收的。"""
        _, html = notify.build_award_email(
            [{"date": "2026-11-20", "id": "0961", "nameZh": "三海地鼠", "shiny": False}],
            self.POKEDEX, streak=4, total=9, source_text="微信读书 30 分钟")
        self.assertIn("2026-11-20", html)

    def test_award_email_shows_progress(self):
        _, html = notify.build_award_email(
            [{"date": "2026-11-23", "id": "0961", "nameZh": "三海地鼠", "shiny": False}],
            self.POKEDEX, streak=4, total=9, source_text="微信读书 30 分钟")
        self.assertIn("9", html)
        self.assertIn("1028", html)

    def test_award_email_for_many_pokemon(self):
        subject, html = notify.build_award_email(
            [{"date": "2026-11-21", "id": "0471", "nameZh": "冰伊布", "shiny": False},
             {"date": "2026-11-22", "id": "0163", "nameZh": "咕咕", "shiny": False}],
            {}, streak=4, total=9, source_text="微信读书 30 分钟")
        self.assertIn("2", subject)
        self.assertIn("冰伊布", html)
        self.assertIn("咕咕", html)

    def test_streak_email_states_the_streak(self):
        subject, html = notify.build_streak_email(missing=1, streak=4, per_award=2)
        self.assertIn("连读", subject)
        self.assertIn("4", html)
        self.assertIn("1", html)

    def test_streak_email_says_it_is_not_a_punishment(self):
        """对齐「未达标 = 没有惩罚」的设计 —— 别吓她。"""
        _, html = notify.build_streak_email(missing=1, streak=4, per_award=2)
        self.assertIn("没有惩罚", html)

    def test_streak_email_gives_a_readable_minutes_figure(self):
        """官方 900 秒 = 1 额度，但官方漏计三成 ⇒ 文案写 20 分钟不是 15。"""
        _, html = notify.build_streak_email(missing=1, streak=4, per_award=2)
        self.assertIn("20 分钟", html)


class SendMailTest(unittest.TestCase):
    """发信：不配 key 就跳过；网络炸了不许抛异常。"""

    def test_without_api_key_it_skips_quietly(self):
        self.assertFalse(notify.send_email("t", "<p>h</p>", api_key="", to_addr="a@b.c"))

    def test_without_recipient_it_skips_quietly(self):
        self.assertFalse(notify.send_email("t", "<p>h</p>", api_key="re_x", to_addr=""))

    def test_it_posts_to_resend(self):
        """用 opener 假造一次发送，检查请求体。"""
        captured = {}

        class FakeResp:
            def read(self):
                return b'{"id":"abc"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_opener(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["headers"] = req.headers
            return FakeResp()

        ok = notify.send_email("标题", "<p>正文</p>", api_key="re_x",
                                to_addr="me@example.com", opener=fake_opener)
        self.assertTrue(ok)
        self.assertEqual(captured["url"], "https://api.resend.com/emails")
        self.assertEqual(captured["body"]["subject"], "标题")
        self.assertEqual(captured["body"]["to"], ["me@example.com"])
        self.assertIn("re_x", str(captured["headers"]))

    def test_a_network_error_does_not_raise(self):
        def boom(req, timeout=None):
            raise OSError("network down")
        self.assertFalse(notify.send_email("t", "<p>h</p>", api_key="re_x",
                                            to_addr="me@example.com", opener=boom))


class MainFlowTest(unittest.TestCase):
    """端到端：读 dex.json → 判定 → 发 → 记幂等。"""

    def _dex(self, **kw):
        base = {
            "version": 2,
            "collected": [],
            "streak": 0,
            "today": {"date": "2026-11-23", "seconds": 0, "threshold": 1800,
                      "sources": {}, "credit": 0, "perAward": 2},
        }
        base.update(kw)
        return base

    def _write_dex(self, d, dex):
        p = os.path.join(d, "dex.json")
        pathlib.Path(p).write_text(json.dumps(dex, ensure_ascii=False), encoding="utf-8")
        return p

    def test_a_new_pokemon_triggers_one_email(self):
        dex = self._dex(collected=[{"date": "2026-11-23", "id": "0961",
                                    "nameZh": "三海地鼠", "shiny": False}])
        d = tempfile.mkdtemp()
        sent = []
        def fake_sender(subject, html, **kw):
            sent.append(subject)
            return True
        res = notify.main(dex_path=self._write_dex(d, dex),
                          state_path=os.path.join(d, "state.json"),
                          now=datetime.datetime(2026, 11, 23, 10, 0),
                          sender=fake_sender)
        self.assertEqual(len(sent), 1)
        self.assertIn("三海地鼠", sent[0])
        self.assertEqual(res["sent"], sent)

    def test_running_twice_does_not_send_twice(self):
        """★ 幂等 —— 第二轮不该再发一遍。"""
        dex = self._dex(collected=[{"date": "2026-11-23", "id": "0961",
                                    "nameZh": "三海地鼠", "shiny": False}])
        d = tempfile.mkdtemp()
        dex_p = self._write_dex(d, dex)
        st_p = os.path.join(d, "state.json")
        sent = []
        def fake_sender(subject, html, **kw):
            sent.append(subject)
            return True
        notify.main(dex_path=dex_p, state_path=st_p,
                    now=datetime.datetime(2026, 11, 23, 10, 0), sender=fake_sender)
        notify.main(dex_path=dex_p, state_path=st_p,
                    now=datetime.datetime(2026, 11, 23, 10, 30), sender=fake_sender)
        self.assertEqual(len(sent), 1)

    def test_streak_warning_is_sent_after_nine_pm(self):
        dex = self._dex(streak=4)
        d = tempfile.mkdtemp()
        sent = []
        def fake_sender(subject, html, **kw):
            sent.append(subject)
            return True
        notify.main(dex_path=self._write_dex(d, dex),
                    state_path=os.path.join(d, "state.json"),
                    now=datetime.datetime(2026, 11, 23, 21, 30), sender=fake_sender)
        self.assertEqual(len(sent), 1)
        self.assertIn("连读", sent[0])

    def test_streak_warning_is_not_sent_in_the_morning(self):
        dex = self._dex(streak=4)
        d = tempfile.mkdtemp()
        sent = []
        def fake_sender(subject, html, **kw):
            sent.append(subject)
            return True
        notify.main(dex_path=self._write_dex(d, dex),
                    state_path=os.path.join(d, "state.json"),
                    now=datetime.datetime(2026, 11, 23, 9, 0), sender=fake_sender)
        self.assertEqual(sent, [])

    def test_streak_warning_only_once_per_day(self):
        dex = self._dex(streak=4)
        d = tempfile.mkdtemp()
        dex_p = self._write_dex(d, dex)
        st_p = os.path.join(d, "state.json")
        sent = []
        def fake_sender(subject, html, **kw):
            sent.append(subject)
            return True
        notify.main(dex_path=dex_p, state_path=st_p,
                    now=datetime.datetime(2026, 11, 23, 21, 30), sender=fake_sender)
        notify.main(dex_path=dex_p, state_path=st_p,
                    now=datetime.datetime(2026, 11, 23, 22, 30), sender=fake_sender)
        self.assertEqual(len(sent), 1)

    def test_nothing_to_say_sends_nothing(self):
        dex = self._dex()
        d = tempfile.mkdtemp()
        sent = []
        def fake_sender(subject, html, **kw):
            sent.append(subject)
            return True
        notify.main(dex_path=self._write_dex(d, dex),
                    state_path=os.path.join(d, "state.json"),
                    now=datetime.datetime(2026, 11, 23, 21, 30), sender=fake_sender)
        self.assertEqual(sent, [])

    def test_a_failed_send_does_not_mark_it_as_notified(self):
        """发失败 ⇒ 下轮还要重发（宁可重发不要漏发）。"""
        dex = self._dex(collected=[{"date": "2026-11-23", "id": "0961",
                                    "nameZh": "三海地鼠", "shiny": False}])
        d = tempfile.mkdtemp()
        dex_p = self._write_dex(d, dex)
        st_p = os.path.join(d, "state.json")
        def failing(subject, html, **kw):
            return False
        notify.main(dex_path=dex_p, state_path=st_p,
                    now=datetime.datetime(2026, 11, 23, 10, 0), sender=failing)
        st = notify.load_state(st_p)
        self.assertEqual(st["awarded"], [])


if __name__ == "__main__":
    unittest.main()
