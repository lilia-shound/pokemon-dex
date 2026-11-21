--[[--
report.lua 的断言集 —— **纯 Lua**，不依赖 fengari 的任何专属 API。

怎么跑：
    node tests/koreader_lua_runner.js        （沙箱里没有真解释器，用 fengari）
    lua   tests/koreader_lua_spec.lua         （哪天有真解释器了也能直接跑）

runner 注入三个全局变量：
    REPORT_PATH   —— report.lua 的路径（不给就用仓库根目录下的相对路径）
    DAYS_FIXTURE  —— 跨语言共用的天数串（内容来自 tests/fixtures/koreader_days.txt）
    WIRE_CASES    —— 线格式的用例（一行一条，换行分隔；
                     内容来自 tests/fixtures/koreader_wire_cases.txt）

注意：这个文件**不许 require 任何 KOReader 模块**，也**不许用 io.open**
（fengari 的 io 库是残的，io.open 是 nil）—— 所有外部输入都由 runner 注入。
--]]--

local Report = dofile(REPORT_PATH or "plugin/koreader-dex.koplugin/report.lua")

-- 跨语言共用的 fixture：日期:分钟，逗号分隔，日期倒序
local FIXTURE = (DAYS_FIXTURE or "2026-11-21:25,2026-11-20:50"):match("^%s*(.-)%s*$")

local passed, failed = 0, 0
local failures = {}
local case_names = {}

-- runner 的 --daily-sql 模式会把 stdout 只留给 SQL ⇒ 那时别打印用例明细
local QUIET = (SILENT == "1")

--- 断言两个值相等（不等就抛错，错误信息里写清楚期望与实际）
local function eq(actual, expected, what)
    if actual ~= expected then
        error((what or "断言") .. "：期望 " .. tostring(expected) .. "，实际 " .. tostring(actual), 2)
    end
end

--- 断言条件成立
local function ok(cond, what)
    if not cond then
        error((what or "断言") .. "：条件不成立", 2)
    end
end

--- 跑一条用例：失败不中断整套（要看到全部失败项，而不是修一条跑一次）
local function case(name, fn)
    case_names[#case_names + 1] = name
    local good, err = pcall(fn)
    if good then
        passed = passed + 1
        if not QUIET then print("PASS  " .. name) end
    else
        failed = failed + 1
        failures[#failures + 1] = { name = name, err = tostring(err) }
        if not QUIET then print("FAIL  " .. name .. " :: " .. tostring(err)) end
    end
end

--- 只含可打印 ASCII（没有空格、没有控制字符、没有中文）⇒ URL 安全
local function is_url_safe(s)
    for i = 1, #s do
        local b = s:byte(i)
        if b < 33 or b > 126 then return false end
    end
    return true
end

--- 判断一个天数串是不是「日期:分钟」逗号分隔
--- （Lua 的模式里，量词只作用于单个字符类，不能作用于捕获组 ⇒ 自己切段）
local function is_wire(s)
    if type(s) ~= "string" or s == "" then return false end
    local rest = s
    local n = 0
    while true do
        local chunk, tail = rest:match("^([^,]*)(.*)$")
        if chunk == nil then return false end
        if chunk:match("^%d%d%d%d%-%d%d%-%d%d:%d+$") == nil then return false end
        n = n + 1
        if tail == "" then break end
        if tail:sub(1, 1) ~= "," then return false end
        rest = tail:sub(2)
        if rest == "" then return false end -- 末尾多一个逗号
    end
    return n > 0
end

------------------------------------------------------------------
-- encode_days：{["2026-11-21"]=25} → "2026-11-21:25,2026-11-20:50"
------------------------------------------------------------------

case("encode_days 空表得到空串", function()
    eq(Report.encode_days({}), "", "空表")
    eq(Report.encode_days(nil), "", "nil")
end)

case("encode_days 单天", function()
    eq(Report.encode_days({ ["2026-11-21"] = 25 }), "2026-11-21:25", "单天")
end)

case("encode_days 多天按日期倒序", function()
    local got = Report.encode_days({
        ["2026-11-20"] = 50,
        ["2026-11-21"] = 25,
        ["2026-11-19"] = 10,
    })
    eq(got, "2026-11-21:25,2026-11-20:50,2026-11-19:10", "倒序")
end)

case("encode_days 分钟向下取整", function()
    eq(Report.encode_days({ ["2026-11-21"] = 25.9 }), "2026-11-21:25", "小数分钟")
end)

case("encode_days 认不出数字的值当 0", function()
    eq(Report.encode_days({ ["2026-11-21"] = "abc" }), "2026-11-21:0", "非数字")
end)

------------------------------------------------------------------
-- decode_days：encode_days 的逆操作（手机落地页那一侧也要能解）
------------------------------------------------------------------

case("decode_days 能解开 fixture", function()
    local days = Report.decode_days(FIXTURE)
    eq(days["2026-11-21"], 25, "2026-11-21 的分钟")
    eq(days["2026-11-20"], 50, "2026-11-20 的分钟")
end)

case("decode_days 空串得到空表", function()
    local n = 0
    for _ in pairs(Report.decode_days("")) do n = n + 1 end
    eq(n, 0, "空串的条目数")
    local m = 0
    for _ in pairs(Report.decode_days(nil)) do m = m + 1 end
    eq(m, 0, "nil 的条目数")
end)

case("decode_days 忽略坏块", function()
    local days = Report.decode_days("abc,2026-1-1:5,2026-11-21:,2026-11-21:xx,2026-11-22:7")
    eq(days["2026-11-22"], 7, "好的一块要留下")
    eq(days["2026-11-21"], nil, "坏的一块不能变成 0 分钟的一天")
    eq(days["2026-1-1"], nil, "日期格式不对的不能收")
end)

case("decode_days 段里有空白就整段丢掉（不 trim）", function()
    -- 规则是「段必须完全匹配」：带空白的段整段丢掉，不做容错
    local days = Report.decode_days("2026-11-21:25, 2026-11-20:50")
    eq(days["2026-11-21"], 25, "没带空白的那一段要留下")
    eq(days["2026-11-20"], nil, "前面带空白的段要整段丢掉")
    eq(Report.decode_days(" 2026-11-21:25")["2026-11-21"], nil, "段前面有空白就不收")
    eq(Report.decode_days("2026-11-21:25 ")["2026-11-21"], nil, "段后面有空白就不收")
    eq(Report.decode_days("2026-11-21:2 5")["2026-11-21"], nil, "分钟中间有空白就不收")
end)

case("decode_days 只认 ASCII 数字（不认 Unicode 数字）", function()
    -- Lua 的 %d 本来就只认 ASCII 0-9；Python 侧要用 [0-9] 才跟得住
    eq(next(Report.decode_days("٢٠٢٦-11-21:5")), nil, "Unicode 数字的日期不能收")
    eq(next(Report.decode_days("2026-11-21:٢٥")), nil, "Unicode 数字的分钟不能收")
end)

case("decode_days 空段直接跳过（不影响别的段）", function()
    local days = Report.decode_days("2026-11-21:25,,2026-11-20:50")
    eq(days["2026-11-21"], 25, "第一段")
    eq(days["2026-11-20"], 50, "第二段")
    eq(Report.decode_days("2026-11-21:25,")["2026-11-21"], 25, "末尾多一个逗号")
    eq(Report.decode_days(",2026-11-21:25")["2026-11-21"], 25, "开头多一个逗号")
end)

case("encode/decode 往返一致", function()
    local days = Report.decode_days(FIXTURE)
    eq(Report.encode_days(days), FIXTURE, "往返")
end)

------------------------------------------------------------------
-- short_url：二维码里那条落地页短链
------------------------------------------------------------------

case("short_url 是基址加上 payload", function()
    local url = Report.short_url({ ["2026-11-21"] = 25, ["2026-11-20"] = 50 })
    eq(url, Report.BASE_URL .. "2026-11-21:25,2026-11-20:50", "短链")
    ok(url:sub(1, #Report.BASE_URL) == Report.BASE_URL, "短链必须以 BASE_URL 开头")
end)

case("short_url 里没有空格和中文（URL 安全）", function()
    local url = Report.short_url({ ["2026-11-21"] = 25, ["2026-11-20"] = 50 })
    ok(is_url_safe(url), "短链里出现了不可打印 ASCII 之外的字符")
    ok(not url:find("%s"), "短链里不能有空白")
    ok(url:find("https://"), "短链必须是 https")
    local payload = url:match("days=(.*)$")
    ok(payload ~= nil, "短链里要带 days= 参数")
    ok(is_wire(payload), "days= 的内容必须是「日期:分钟」逗号分隔")
end)

case("BASE_URL 与 SOURCE_ID 是定值", function()
    eq(Report.BASE_URL, "https://lilia-shound.github.io/pokemon-dex/koreader/?days=", "BASE_URL")
    eq(Report.SOURCE_ID, "koreader", "SOURCE_ID（服务端注册表里要同名）")
end)

------------------------------------------------------------------
-- to_minutes：秒 → 分钟（向下取整，跟服务端的 floor 口径一致）
------------------------------------------------------------------

case("to_minutes 向下取整", function()
    eq(Report.to_minutes(119), 1, "119 秒")
    eq(Report.to_minutes(120), 2, "120 秒")
    eq(Report.to_minutes(59), 0, "59 秒")
    eq(Report.to_minutes(0), 0, "0 秒")
end)

case("to_minutes 认不出数就当 0", function()
    eq(Report.to_minutes(nil), 0, "nil")
    eq(Report.to_minutes("abc"), 0, "非数字")
    eq(Report.to_minutes("300"), 5, "数字字符串")
end)

------------------------------------------------------------------
-- pick_days：取最近 N 天（有分钟数的才要），日期倒序
------------------------------------------------------------------

local ALL_DAYS = {
    ["2026-11-21"] = 25,
    ["2026-11-20"] = 50,
    ["2026-11-19"] = 0,     -- 0 分钟的天要丢掉
    ["2026-11-18"] = 10,
    ["2026-11-17"] = 5,
}

case("pick_days 只取最近 N 天", function()
    local got = Report.pick_days(ALL_DAYS, 2)
    eq(Report.encode_days(got), "2026-11-21:25,2026-11-20:50", "最近 2 天")
end)

case("pick_days 丢掉 0 分钟的天", function()
    local got = Report.pick_days(ALL_DAYS, 10)
    eq(Report.encode_days(got), "2026-11-21:25,2026-11-20:50,2026-11-18:10,2026-11-17:5", "全部非零的天")
end)

case("pick_days 不给上限就全要", function()
    local got = Report.pick_days(ALL_DAYS, nil)
    eq(Report.encode_days(got), "2026-11-21:25,2026-11-20:50,2026-11-18:10,2026-11-17:5", "全部")
end)

case("pick_days 上限比天数大就全要", function()
    local got = Report.pick_days(ALL_DAYS, 99)
    eq(Report.encode_days(got), "2026-11-21:25,2026-11-20:50,2026-11-18:10,2026-11-17:5", "全部")
end)

case("pick_days 上限是 0 就一天也不要", function()
    local got = Report.pick_days(ALL_DAYS, 0)
    eq(Report.encode_days(got), "", "空")
end)

case("pick_days 上限不是数字就全要（不抛错）", function()
    -- Lua 5.3 的 math.min 不接受字符串 ⇒ 上限要 tonumber 兜一下
    local got = Report.pick_days(ALL_DAYS, "abc")
    eq(Report.encode_days(got), "2026-11-21:25,2026-11-20:50,2026-11-18:10,2026-11-17:5", "全部")
end)

case("pick_days 上限是数字字符串也能用", function()
    local got = Report.pick_days(ALL_DAYS, "2")
    eq(Report.encode_days(got), "2026-11-21:25,2026-11-20:50", "最近 2 天")
end)

------------------------------------------------------------------
-- credits：这些分钟一共折算多少个额度
------------------------------------------------------------------

case("credits 每一天各自向下取整再相加", function()
    -- 与服务端口径一致：额度是**按天**折算的，不跨天凑零头
    eq(Report.credits({ ["2026-11-21"] = 25 }, 15), 1, "25 分钟")
    eq(Report.credits({ ["2026-11-21"] = 29 }, 15), 1, "29 分钟")
    eq(Report.credits({ ["2026-11-21"] = 30 }, 15), 2, "30 分钟")
    eq(Report.credits({ ["2026-11-21"] = 25, ["2026-11-20"] = 25 }, 15), 2, "两天各 25 分钟")
end)

case("credits 空表是 0", function()
    eq(Report.credits({}, 15), 0, "空表")
    eq(Report.credits(nil, 15), 0, "nil")
end)

case("credits per 小于 1 时按 1 算", function()
    eq(Report.credits({ ["2026-11-21"] = 25 }, 0), 25, "per=0")
    eq(Report.credits({ ["2026-11-21"] = 25 }, -3), 25, "per=-3")
end)

case("credits per 认不出就当 1", function()
    eq(Report.credits({ ["2026-11-21"] = 25 }, "abc"), 25, "per 非数字")
end)

case("MAX_MINUTES_PER_DAY 是 1440（一天 24 小时）", function()
    eq(Report.MAX_MINUTES_PER_DAY, 1440, "单日上限（与服务端注册表一致）")
end)

case("credits 单日超过上限要夹住（与服务端口径一致）", function()
    -- 服务端 scripts/sources.py 的 credit_for_day：min(原始量, maxPerDay) 再折算
    eq(Report.credits({ ["2026-11-21"] = 5000 }, 15), 96, "5000 分钟按 1440 分钟算 ⇒ 96 个额度")
    eq(Report.credits({ ["2026-11-21"] = 1440 }, 15), 96, "正好 1440 分钟")
    eq(Report.credits({ ["2026-11-21"] = 1439 }, 15), 95, "1439 分钟")
    eq(Report.credits({ ["2026-11-21"] = -100 }, 15), 0, "负的分钟夹到 0")
end)

case("credits 可以传别的上限", function()
    eq(Report.credits({ ["2026-11-21"] = 5000 }, 15, 60), 4, "上限 60 分钟 ⇒ 4 个额度")
    eq(Report.credits({ ["2026-11-21"] = 5000 }, 15, 0), 0, "上限 0 ⇒ 没有额度")
end)

------------------------------------------------------------------
-- DAILY_SQL：与 KOReader 内置 statistics 的 sqlDaily() 逐字一致
------------------------------------------------------------------

case("DAILY_SQL 按设备本地时区分天", function()
    local sql = Report.DAILY_SQL
    eq(type(sql), "string", "DAILY_SQL 的类型")
    local flat = sql:gsub("%s+", " ")
    ok(flat:find("strftime('%%Y-%%m-%%d', start_time, 'unixepoch', 'localtime')", 1, true) ~= nil,
        "必须用 localtime 按设备本地时区分天")
    ok(flat:find("GROUP BY id_book, page, dates", 1, true) ~= nil,
        "必须按 id_book, page, dates 分组（同一天同一页读多次要累加）")
    ok(flat:find("FROM page_stat", 1, true) ~= nil,
        "必须查 page_stat 视图（会按当前页数重算 reflowable 文档的分页）")
    ok(flat:find("page_stat_data", 1, true) == nil,
        "不能直接查 page_stat_data（那样就不是内置 statistics 的口径了）")
    ok(flat:find("GROUP BY dates", 1, true) ~= nil, "外层要按天分组")
    ok(flat:find("ORDER BY dates DESC", 1, true) ~= nil, "结果按日期倒序")
end)

case("DAILY_SQL 留着一个 %d 的时间戳占位符", function()
    local flat = Report.DAILY_SQL:gsub("%s+", " ")
    ok(flat:find("start_time >= %d", 1, true) ~= nil, "要有 start_time >= %d")
    ok(flat:find("%%Y-%%m-%%d", 1, true) ~= nil, "日期格式要写成 %%Y-%%m-%%d（string.format 的转义）")
end)

------------------------------------------------------------------
-- daily_sql：**唯一**的 SQL 格式化入口
--
-- 这几条是防「静默产出错数据」的护栏：
--   万一有人把格式化改成绑定参数（或者绕过这个函数自己拼 SQL），
--   `%%` 就会原样留在 SQL 里 ⇒ SQLite 的 strftime 把 %% 渲染成字面的 % ⇒
--   每行日期都变成 "%Y-%m-%d"，分钟被汇总到一个假的日子上。
------------------------------------------------------------------

case("daily_sql 把 %d 填成 epoch 并把 %% 解开", function()
    local sql = Report.daily_sql(123)
    ok(sql:find("start_time >= 123", 1, true) ~= nil, "epoch 要填进 start_time >= %d")
    ok(sql:find("strftime('%Y-%m-%d', start_time, 'unixepoch', 'localtime')", 1, true) ~= nil,
        "%%Y-%%m-%%d 必须被解成 %Y-%m-%d")
    ok(sql:find("%%", 1, true) == nil, "格式化后不该还剩 %%")
    -- 格式化后整条 SQL 里只剩 strftime 日期格式的三个 %（模板里是 6 个 %% + 1 个 %d）
    local _, percent = sql:gsub("%%", "")
    eq(percent, 3, "格式化后剩下的百分号个数")
end)

case("daily_sql 的模板没被偷偷改过", function()
    local _, template_percent = Report.DAILY_SQL:gsub("%%", "")
    eq(template_percent, 7, "模板里应该是 6 个 %%（日期格式）+ 1 个 %d（epoch）")
end)

case("daily_sql 对小数与非数字都取整", function()
    ok(Report.daily_sql(123.7):find("start_time >= 123", 1, true) ~= nil, "小数要取整")
    ok(Report.daily_sql(nil):find("start_time >= 0", 1, true) ~= nil, "nil 当 0")
    ok(Report.daily_sql("abc"):find("start_time >= 0", 1, true) ~= nil, "非数字当 0")
end)

case("daily_sql 负的 epoch 也照样填进去", function()
    ok(Report.daily_sql(-1):find("start_time >= -1", 1, true) ~= nil, "负数")
end)

------------------------------------------------------------------
-- collect_days / collect_pages：把查询结果整理成 {日期 = 分钟/页数}
--
-- ljsqlite3 的 conn:exec() 返回的是**「每一列一个数组」的表**，不是行数组：
--     res[1][i] = 日期、res[2][i] = 页数、res[3][i] = 秒数、res[4][i] = 时间戳
------------------------------------------------------------------

-- 一份典型的查询结果（DAILY_SQL 的四列）
local RES = {
    { "2026-11-21", "2026-11-20" },        -- 第 1 列：日期
    { 3, 8 },                              -- 第 2 列：页数
    { 1500, 720 },                         -- 第 3 列：秒数
    { 1766275200, 1766188800 },            -- 第 4 列：起始时间戳
}

case("collect_days 把秒数折成分钟", function()
    local days = Report.collect_days(RES)
    eq(days["2026-11-21"], 25, "2026-11-21 的分钟")
    eq(days["2026-11-20"], 12, "2026-11-20 的分钟")
end)

case("collect_days 分钟向下取整", function()
    local days = Report.collect_days({
        { "2026-11-21" }, { 1 }, { 119 }, { 0 },
    })
    eq(days["2026-11-21"], 1, "119 秒 → 1 分钟")
end)

case("collect_days 0 分钟的天留着（要不要报交给 pick_days 决定）", function()
    local days = Report.collect_days({
        { "2026-11-21" }, { 1 }, { 59 }, { 0 },
    })
    eq(days["2026-11-21"], 0, "59 秒 → 0 分钟，但那天还在表里")
    -- pick_days 会把 0 分钟的天丢掉
    eq(next(Report.pick_days(days, 7)), nil, "pick_days 之后就没了")
end)

case("collect_days 认不出的数当 0", function()
    local days = Report.collect_days({
        { "2026-11-21" }, { 1 }, { "abc" }, { 0 },
    })
    eq(days["2026-11-21"], 0, "非数字的秒数当 0")
end)

case("collect_days 缺列/空表不炸", function()
    eq(next(Report.collect_days(nil)), nil, "nil")
    eq(next(Report.collect_days({})), nil, "空表")
    -- 只有日期列（秒数列整个没有）⇒ 那天的秒数当 0
    local only_dates = Report.collect_days({ { "2026-11-21" } })
    eq(only_dates["2026-11-21"], 0, "只有日期列 ⇒ 0 分钟")
    -- 各列的行数对不上 ⇒ 少的那个当 0，不抛错
    local days = Report.collect_days({
        { "2026-11-21", "2026-11-20" }, { 3 }, { 1500 }, { 0 },
    })
    eq(days["2026-11-21"], 25, "第一行照常")
    eq(days["2026-11-20"], 0, "第二行没有秒数 ⇒ 0 分钟")
end)

case("collect_pages 把第 2 列整理成页数", function()
    local pages = Report.collect_pages(RES)
    eq(pages["2026-11-21"], 3, "2026-11-21 的页数")
    eq(pages["2026-11-20"], 8, "2026-11-20 的页数")
    eq(next(Report.collect_pages(nil)), nil, "nil")
end)

------------------------------------------------------------------
-- filter_dates：只留下落在某个日历日窗口里的天
------------------------------------------------------------------

case("filter_dates 按日期窗口切一刀", function()
    local days = {
        ["2026-11-21"] = 25,
        ["2026-11-20"] = 50,
        ["2026-11-01"] = 10,
    }
    local got = Report.filter_dates(days, "2026-11-15", "2026-11-21")
    eq(Report.encode_days(got), "2026-11-21:25,2026-11-20:50", "窗口里的两天")
end)

case("filter_dates 两端都含", function()
    local days = { ["2026-11-21"] = 25, ["2026-11-15"] = 10 }
    local got = Report.filter_dates(days, "2026-11-15", "2026-11-21")
    eq(Report.encode_days(got), "2026-11-21:25,2026-11-15:10", "两端都在")
end)

case("filter_dates nil 边界 = 不限", function()
    local days = { ["2026-11-21"] = 25 }
    eq(Report.encode_days(Report.filter_dates(days, nil, nil)), "2026-11-21:25", "都不限")
    eq(Report.encode_days(Report.filter_dates(days, "2026-11-22", nil)), "", "下界在后面 ⇒ 空")
    eq(Report.encode_days(Report.filter_dates(nil, "2026-01-01", nil)), "", "nil")
end)

------------------------------------------------------------------
-- 跨语言探针：Python 侧要拿同一份结果做对照
------------------------------------------------------------------

local function json_str(s)
    local out = tostring(s or ""):gsub('[%c"\\]', function(c)
        if c == '"' then return '\\"' end
        if c == "\\" then return "\\\\" end
        if c == "\n" then return "\\n" end
        if c == "\r" then return "\\r" end
        if c == "\t" then return "\\t" end
        return ""
    end)
    return '"' .. out .. '"'
end

local function json_days(days)
    local keys = {}
    for d in pairs(days or {}) do keys[#keys + 1] = d end
    table.sort(keys)
    local parts = {}
    for _, d in ipairs(keys) do
        parts[#parts + 1] = '"' .. d .. '":' .. math.floor(tonumber(days[d]) or 0)
    end
    return "{" .. table.concat(parts, ",") .. "}"
end

--- 给 Python 侧用的探针：把一个天数串过一遍 report.lua，返回 JSON
---
--- **不做 trim** —— 线格式的规则是「段必须完全匹配」，带空白就是坏段；
--- 在探针里偷偷 trim 会把两边的不一致盖掉。
--- @param wire string 形如 "2026-11-21:25,2026-11-20:50"
--- @return string JSON
local function probe(wire)
    local days = Report.decode_days(wire)
    local encoded = Report.encode_days(days)
    local per = tonumber(KOREADER_PER) or 15
    return "{" ..
        '"encoded":' .. json_str(encoded) .. "," ..
        '"decoded":' .. json_days(days) .. "," ..
        '"short_url":' .. json_str(Report.short_url(days)) .. "," ..
        '"credits":' .. tostring(Report.credits(days, per)) .. "," ..
        '"max_per_day":' .. tostring(Report.MAX_MINUTES_PER_DAY) .. "," ..
        '"credits_over_cap":' .. tostring(Report.credits({ ["2026-11-21"] = 5000 }, per)) ..
        "}"
end

--- 把 WIRE_CASES 里的每一条线都过一遍，返回 JSON 数组
---
--- 好坏线都在里面 —— 跨语言对拍要**逐例**比，
--- 只喂干净的 fixture 的话，空白 / Unicode 数字这类分歧根本看不见。
--- @return string JSON
local function probe_cases()
    local out = {}
    for wire in tostring(WIRE_CASES or ""):gmatch("[^\n]+") do
        local days = Report.decode_days(wire)
        out[#out + 1] = '{"wire":' .. json_str(wire) ..
                        ',"decoded":' .. json_days(days) ..
                        ',"encoded":' .. json_str(Report.encode_days(days)) .. '}'
    end
    return "[" .. table.concat(out, ",") .. "]"
end

return {
    passed = passed,
    failed = failed,
    failures = failures,
    cases = case_names,
    probe = probe,
    probe_cases = probe_cases,
    -- 给 runner 的 --daily-sql 模式用：设备上真正执行的那条 SQL
    daily_sql = function(since) return Report.daily_sql(since) end,
    -- 给 runner 的 --collect-days 模式用：结果表由 runner 塞进全局 RES_TABLE
    collect_days = function() return Report.collect_days(RES_TABLE) end,
    collect_pages = function() return Report.collect_pages(RES_TABLE) end,
}
