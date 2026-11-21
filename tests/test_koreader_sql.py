"""用 Python 的 sqlite3 **真跑一遍**插件里那条 SQL（口径测试）。

SQL 不是在测试里另抄一份 —— 是从 `plugin/koreader-dex.koplugin/koreader_dex_report.lua`
里抽出来的（抄一份会把「插件改了、测试没改」这件事盖住）。

**而且跑的就是 Lua 侧（koreader_dex_report.lua 的 `Report.daily_sql`）格式化出来的那条** ——
「测试执行的语句」与「设备上执行的语句」是同一条，不是两份各自维护的复刻。
（`format_sql()` 留在这儿当**对照的独立实现**：两边必须字节级一致。）

假库照 KOReader 内置 statistics 插件的 schema 造（schema 版本 20221111，
见 plugins/statistics.koplugin/main.lua）：
    book             —— 书架里的书（page_stat 通过 id_book 关联它）
    page_stat_data   —— 每次翻页落的一条记录
    numbers          —— 1..1000，给 page_stat 视图拆页用
    page_stat        —— **视图**：按书当前的总页数重算 reflowable 文档的分页

插件那条 SQL 查的是 `page_stat` 视图而不是 `page_stat_data` ——
内置 statistics 就是这么算的（口径一致 ⇒ 她对账时数字对得上）。
"""

import calendar
import contextlib
import os
import pathlib
import re
import sqlite3
import time
import unittest

from tests import koreader_lua_bridge as bridge

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORT_LUA = ROOT / "plugin" / "koreader-dex.koplugin" / "koreader_dex_report.lua"

# 假库里放的几个固定时刻（UTC 的 epoch）
# 2026-11-21T00:00:00Z
DAY_START = calendar.timegm((2026, 11, 21, 0, 0, 0, 0, 0, 0))
# 2026-11-21T20:00:00Z —— UTC 是 21 号，UTC+8 已经是 22 号凌晨 4 点
LATE_UTC = calendar.timegm((2026, 11, 21, 20, 0, 0, 0, 0, 0))
# 2026-11-21T15:50:00Z —— UTC+8 下是 21 号 23:50（跨零点的那次阅读）
LATE_LOCAL_NIGHT = calendar.timegm((2026, 11, 21, 15, 50, 0, 0, 0, 0))

# --- KOReader 内置 statistics 的 DDL（schema 20221111），逐字照抄 -------------

BOOK_DDL = """
    CREATE TABLE IF NOT EXISTS book
        (
            id integer PRIMARY KEY autoincrement,
            title text,
            authors text,
            notes      integer,
            last_open  integer,
            highlights integer,
            pages      integer,
            series text,
            language text,
            md5 text,
            total_read_time  integer,
            total_read_pages integer
        );
"""

PAGE_STAT_DATA_DDL = """
    CREATE TABLE IF NOT EXISTS page_stat_data
        (
            id_book     integer,
            page        integer NOT NULL DEFAULT 0,
            start_time  integer NOT NULL DEFAULT 0,
            duration    integer NOT NULL DEFAULT 0,
            total_pages integer NOT NULL DEFAULT 0,
            UNIQUE (id_book, page, start_time),
            FOREIGN KEY(id_book) REFERENCES book(id)
        );
    CREATE INDEX IF NOT EXISTS page_stat_data_start_time ON page_stat_data(start_time);
"""

NUMBERS_AND_VIEW_DDL = """
    CREATE TABLE IF NOT EXISTS numbers
        (
            number INTEGER PRIMARY KEY
        );
    WITH RECURSIVE counter AS
        (
            SELECT 1 as N UNION ALL
            SELECT N + 1 FROM counter WHERE N < 1000
        )
    INSERT INTO numbers SELECT N AS number FROM counter;

    CREATE VIEW IF NOT EXISTS page_stat AS
        SELECT id_book, first_page + idx - 1 AS page, start_time, duration / (last_page - first_page + 1) AS duration
        FROM (
            SELECT id_book, page, total_pages, pages, start_time, duration,
                ((page - 1) * pages) / total_pages + 1 AS first_page,
                max(((page - 1) * pages) / total_pages + 1, (page * pages) / total_pages) AS last_page,
                idx
            FROM page_stat_data
            JOIN book ON book.id = id_book
            JOIN (SELECT number as idx FROM numbers) AS N ON idx <= (last_page - first_page + 1)
        );
"""

DAILY_SQL_RE = re.compile(r"Report\.DAILY_SQL\s*=\s*\[\[(.*?)\]\]", re.S)


def extract_daily_sql(text: str) -> str:
    """从 koreader_dex_report.lua 源码里把 DAILY_SQL 抽出来。

    **不在测试里另抄一份 SQL** —— 抄了的话，插件改了 SQL 而测试没改，
    口径就悄悄跑偏了，还全绿。

    还要照抄 Lua 长字符串的规矩：**`[[` 后面紧跟的那个换行不算内容**
    （Lua 5.3 手册：长括号开头的那个换行会被跳过）。
    漏了这一条 ⇒ 抽出来的字符串比 Lua 里的多一个换行 ⇒
    「设备上跑的那条 SQL」与「测试里跑的那条 SQL」就不是同一条了
    （字节级比对会当场抓住）。
    """
    m = DAILY_SQL_RE.search(text)
    if not m:
        raise AssertionError("koreader_dex_report.lua 里找不到 Report.DAILY_SQL = [[...]]")
    body = m.group(1)
    if body.startswith("\n"):
        body = body[1:]
    return body


def format_sql(template: str, since: int) -> str:
    """照 Lua 的 `string.format` 处理这条模板（**独立实现**，只用来对照）。

    这条模板里只有两种占位符：
        %d   —— 要填进去的 epoch 秒（since）
        %%   —— 转义成字面的 %（所以 %%Y-%%m-%%d → %Y-%m-%d）
    出现别的占位符就直接报错 —— 宁可当场炸，也不要悄悄填错。

    **测试实际执行的不是它**（执行的是 Lua 侧 `Report.daily_sql` 的输出），
    它的作用是当第二只眼睛：两边算出来的 SQL 必须**逐字节**一样。
    """
    out = []
    i = 0
    while i < len(template):
        ch = template[i]
        if ch != "%":
            out.append(ch)
            i += 1
            continue
        nxt = template[i + 1:i + 2]
        if nxt == "%":
            out.append("%")
            i += 2
        elif nxt == "d":
            out.append(str(int(since)))
            i += 2
        else:
            raise AssertionError("DAILY_SQL 里有没见过的占位符：%%" + nxt)
    return "".join(out)


@contextlib.contextmanager
def device_timezone(tz: str):
    """把「设备本地时区」临时改成某个时区。

    SQLite 的 'localtime' 修饰符走的是 libc 的 localtime_r，
    所以改 TZ + tzset 就能让 SQL 里的 'localtime' 真的换时区。
    """
    old = os.environ.get("TZ")
    os.environ["TZ"] = tz
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def make_db() -> sqlite3.Connection:
    """造一个 KOReader statistics 插件的真·假库。"""
    db = sqlite3.connect(":memory:")
    db.executescript(BOOK_DDL)
    db.executescript(PAGE_STAT_DATA_DDL)
    db.executescript(NUMBERS_AND_VIEW_DDL)
    return db


def add_book(db: sqlite3.Connection, book_id: int, pages: int, title: str = "测试书") -> None:
    db.execute(
        "INSERT INTO book (id, title, authors, pages) VALUES (?, ?, ?, ?)",
        (book_id, title, "某作者", pages),
    )


def add_reading(db: sqlite3.Connection, id_book: int, page: int, start_time: int,
                duration: int, total_pages: int) -> None:
    """落一条「读了一页多久」的记录（跟内置 statistics 落库的是同一种东西）。"""
    db.execute(
        "INSERT INTO page_stat_data (id_book, page, start_time, duration, total_pages)"
        " VALUES (?, ?, ?, ?, ?)",
        (id_book, page, start_time, duration, total_pages),
    )


class DailySqlTest(unittest.TestCase):
    """插件那条 DAILY_SQL 的口径。"""

    @classmethod
    def setUpClass(cls):
        cls.sql_template = extract_daily_sql(REPORT_LUA.read_text(encoding="utf-8"))
        cls.node = bridge.find_node()
        cls.has_lua = bool(cls.node) and bridge.fengari_available(cls.node)

    # 设备在台州 = UTC+8（POSIX 的 TZ 写法里是 UTC-8，符号是反的）
    DEVICE_TZ = "UTC-8"

    def daily_sql(self, since: int) -> str:
        """**设备上真正执行的那条 SQL**。

        优先用 Lua 侧（koreader_dex_report.lua 的 `Report.daily_sql`）格式化出来的 ——
        这样「测试跑的」与「设备跑的」是同一条语句，
        而不是两份各自维护、慢慢漂移的复刻。
        没有 node/fengari 时才退回 Python 的复刻
        （那种情况下「字节级一致」那条用例会跳过）。
        """
        if self.has_lua:
            return bridge.lua_daily_sql(self.node, since)
        return format_sql(self.sql_template, since)

    def run_daily(self, db: sqlite3.Connection, since: int = 0) -> list:
        """按插件那条 SQL 查一遍，返回 [(日期, 页数, 秒数, 起始时间), ...]。

        **不**动时区 —— 调用方自己控制（时区分天的用例要自己切）。
        """
        sql = self.daily_sql(since)
        self.last_sql = sql
        return db.execute(sql).fetchall()

    def run_daily_on_device(self, db: sqlite3.Connection, since: int = 0) -> list:
        """按**设备本地时区**（UTC+8）查一遍 —— 断言具体日期时都要走这个。

        否则结果会跟着跑测试的机器的时区变（同一条记录在 UTC 下是 21 号，
        在 UTC+8 下可能是 22 号）—— 断言具体日期就必须是确定的。
        """
        with device_timezone(self.DEVICE_TZ):
            return self.run_daily(db, since)

    # -- SQL 的形状 ------------------------------------------------------

    def test_sql_uses_localtime_and_groups_by_book_page_day(self):
        """口径必须与内置 statistics 的 sqlDaily() 一致。"""
        flat = re.sub(r"\s+", " ", self.sql_template)
        self.assertIn("strftime('%%Y-%%m-%%d', start_time, 'unixepoch', 'localtime')", flat)
        self.assertIn("GROUP BY id_book, page, dates", flat)
        self.assertIn("GROUP BY dates", flat)
        self.assertIn("ORDER BY dates DESC", flat)

    def test_sql_reads_the_view_not_the_raw_table(self):
        """必须查 page_stat 视图（它会按当前页数重算 reflowable 文档的分页）。"""
        flat = re.sub(r"\s+", " ", self.sql_template)
        self.assertIn("FROM page_stat", flat)
        self.assertNotIn("page_stat_data", flat, "直接查原始表就不是内置 statistics 的口径了")

    def test_sql_has_a_since_placeholder(self):
        """模板里要留着 %d 的时间戳占位符（由 Report.daily_sql 填）。"""
        flat = re.sub(r"\s+", " ", self.sql_template)
        self.assertIn("start_time >= %d", flat)

    def test_format_sql_rejects_unknown_placeholders(self):
        """出现没见过的占位符要当场炸，别悄悄填错。"""
        with self.assertRaises(AssertionError):
            format_sql("SELECT %s", 0)

    def test_format_sql_unescapes_the_date_format(self):
        """%%Y-%%m-%%d 是 Lua `string.format` 的转义 ⇒ 格式化后要变成 %Y-%m-%d。"""
        sql = format_sql(self.sql_template, 123)
        self.assertIn("strftime('%Y-%m-%d', start_time, 'unixepoch', 'localtime')", sql)
        self.assertNotIn("%%", sql, "格式化后不该还剩 %%")
        self.assertIn("start_time >= 123", sql)

    # -- 「设备上跑的那条 SQL」是不是就是这里跑的那条 ----------------------

    def test_executed_sql_is_byte_identical_to_the_lua_formatted_sql(self):
        """测试执行的语句必须与 **Lua 格式化出来的**逐字节一致。

        这是「Important #1」的护栏：
        - 执行的那条 == 插件（koreader_dex_report.lua 的 Report.daily_sql）会执行的那条；
        - 两边的格式化语义也一样（Python 复刻 vs Lua 的 string.format）。
        """
        if not self.has_lua:
            self.skipTest("没有 node/fengari，拿不到 Lua 格式化出来的 SQL")
        db = make_db()
        add_book(db, 1, pages=100)
        add_reading(db, 1, page=5, start_time=DAY_START, duration=300, total_pages=100)
        rows = self.run_daily_on_device(db, since=DAY_START)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            self.last_sql.encode("utf-8"),
            bridge.lua_daily_sql(self.node, DAY_START).encode("utf-8"),
            "执行的 SQL 与 Lua 格式化出来的不是同一条",
        )
        self.assertEqual(
            self.last_sql.encode("utf-8"),
            format_sql(self.sql_template, DAY_START).encode("utf-8"),
            "Lua 与 Python 两边算出来的 SQL 不是同一条",
        )

    def test_executed_sql_has_no_leftover_percent_escapes(self):
        """执行的那条 SQL 里不能还剩 %%。

        剩下了 ⇒ SQLite 的 strftime 会把 %% 渲染成字面的 % ⇒
        每行日期都变成 "%Y-%m-%d"，分钟被汇总到一个假的日子上 ——
        **静默产出错数据，只有真机对账才暴露**。
        """
        sql = self.daily_sql(0)
        self.assertNotIn("%%", sql)
        self.assertIn("strftime('%Y-%m-%d', start_time, 'unixepoch', 'localtime')", sql)

    def test_formatted_sql_yields_real_dates_not_the_format_string(self):
        """格式化后的 SQL 查出来的日期是真实日期，不是字面的 "%Y-%m-%d"。"""
        db = make_db()
        add_book(db, 1, pages=100)
        add_reading(db, 1, page=5, start_time=DAY_START, duration=300, total_pages=100)
        rows = self.run_daily_on_device(db)
        self.assertEqual([r[0] for r in rows], ["2026-11-21"])
        self.assertNotEqual(rows[0][0], "%Y-%m-%d")

    # -- 口径本身 --------------------------------------------------------

    def test_same_page_same_day_is_accumulated(self):
        """同一天同一页读了两次 ⇒ 时长累加。"""
        db = make_db()
        add_book(db, 1, pages=100)
        add_reading(db, 1, page=5, start_time=DAY_START, duration=300, total_pages=100)
        add_reading(db, 1, page=5, start_time=DAY_START + 3600, duration=600, total_pages=100)
        rows = self.run_daily_on_device(db)
        self.assertEqual(len(rows), 1)
        dates, pages, durations, _ = rows[0]
        self.assertEqual(dates, "2026-11-21")
        self.assertEqual(durations, 900, "同一页两次读要累加成 900 秒")
        self.assertEqual(pages, 1, "同一天读的是同一页 ⇒ 页数算 1")

    def test_different_pages_same_day_are_counted_separately(self):
        """同一天读了两页 ⇒ 页数算 2，时长各自算完再相加。"""
        db = make_db()
        add_book(db, 1, pages=100)
        add_reading(db, 1, page=5, start_time=DAY_START, duration=300, total_pages=100)
        add_reading(db, 1, page=6, start_time=DAY_START + 60, duration=120, total_pages=100)
        rows = self.run_daily_on_device(db)
        self.assertEqual(len(rows), 1)
        dates, pages, durations, _ = rows[0]
        self.assertEqual(dates, "2026-11-21")
        self.assertEqual(durations, 420)
        self.assertEqual(pages, 2, "两页 ⇒ 页数算 2")

    def test_different_days_are_split(self):
        """两天的记录要分成两行，且日期倒序（最新的一天在最前面）。"""
        db = make_db()
        add_book(db, 1, pages=100)
        add_reading(db, 1, page=5, start_time=DAY_START, duration=300, total_pages=100)
        add_reading(db, 1, page=5, start_time=DAY_START + 86400, duration=300, total_pages=100)
        rows = self.run_daily_on_device(db)
        self.assertEqual([r[0] for r in rows], ["2026-11-22", "2026-11-21"], "日期倒序")

    def test_days_are_split_by_device_local_timezone(self):
        """按**设备本地时区**分天：UTC 是 21 号，UTC+8 已经是 22 号凌晨。"""
        db = make_db()
        add_book(db, 1, pages=100)
        add_reading(db, 1, page=5, start_time=LATE_UTC, duration=300, total_pages=100)
        with device_timezone("UTC"):
            utc_rows = self.run_daily(db)
        with device_timezone("UTC-8"):  # POSIX 写法：UTC+8
            plus8_rows = self.run_daily(db)
        self.assertEqual(utc_rows[0][0], "2026-11-21", "UTC 下是 21 号")
        self.assertEqual(plus8_rows[0][0], "2026-11-22", "UTC+8 下是 22 号")

    def test_reading_across_midnight_stays_on_its_start_day(self):
        """一次读到零点后的阅读，**算在开始那天**（内置 statistics 就是这个口径）。"""
        db = make_db()
        add_book(db, 1, pages=100)
        # UTC+8 下是 21 号 23:50 开始，读了 20 分钟（跨过零点）
        add_reading(db, 1, page=5, start_time=LATE_LOCAL_NIGHT, duration=1200, total_pages=100)
        with device_timezone("UTC-8"):
            rows = self.run_daily(db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "2026-11-21", "跨零点的阅读算在开始那天")
        self.assertEqual(rows[0][2], 1200, "时长照算")

    def test_since_filters_out_older_readings(self):
        """start_time >= %d 那个占位符真的在起作用。"""
        db = make_db()
        add_book(db, 1, pages=100)
        add_reading(db, 1, page=5, start_time=DAY_START, duration=300, total_pages=100)
        add_reading(db, 1, page=6, start_time=LATE_UTC, duration=600, total_pages=100)
        rows = self.run_daily_on_device(db, since=LATE_UTC)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], 600, "早于 since 的那次不该被算进来")

    def test_multiple_books_are_kept_apart(self):
        """GROUP BY id_book ⇒ 两本书的同一页不相加。"""
        db = make_db()
        add_book(db, 1, pages=100, title="书一")
        add_book(db, 2, pages=100, title="书二")
        add_reading(db, 1, page=5, start_time=DAY_START, duration=300, total_pages=100)
        add_reading(db, 2, page=5, start_time=DAY_START, duration=120, total_pages=100)
        rows = self.run_daily_on_device(db)
        self.assertEqual(len(rows), 1)
        dates, pages, durations, _ = rows[0]
        self.assertEqual(durations, 420, "两本书的时长要各自算完再相加")
        self.assertEqual(pages, 2, "两本书各一页")

    def test_view_rescales_reflowable_pages(self):
        """reflowable 文档重排后页数变了 ⇒ 视图会把一条记录摊到好几页上。

        这条用例是给「口径」兜底的：插件查的是 page_stat 视图，
        视图会把 duration 摊开（整数除法会丢零头），
        直接查 page_stat_data 的话数字会不一样 ⇒ 跟内置 statistics 对不上。
        """
        db = make_db()
        add_book(db, 1, pages=300, title="重排过的书")
        # 落库时这本书 100 页，现在重排成 300 页 ⇒ 第 1 页摊成 3 页
        add_reading(db, 1, page=1, start_time=DAY_START, duration=301, total_pages=100)
        rows = self.run_daily_on_device(db)
        self.assertEqual(len(rows), 1)
        dates, pages, durations, _ = rows[0]
        self.assertEqual(pages, 3, "一条记录被摊成 3 页")
        self.assertEqual(durations, 300, "301 秒摊成 3 页 ⇒ 每页 100 秒（整数除法丢零头）")


if __name__ == "__main__":
    unittest.main()
