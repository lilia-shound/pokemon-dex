--[[--
report.lua 的断言集 —— **纯 Lua**，不依赖 fengari 的任何专属 API。

怎么跑：
    node tests/koreader_lua_runner.js        （沙箱里没有真解释器，用 fengari）
    lua   tests/koreader_lua_spec.lua         （哪天有真解释器了也能直接跑）

runner 注入两个全局变量：
    REPORT_PATH   —— report.lua 的路径（不给就用仓库根目录下的相对路径）
    DAYS_FIXTURE  —— 跨语言共用的天数串（内容来自 tests/fixtures/koreader_days.txt）

注意：这个文件**不许 require 任何 KOReader 模块**，也**不许用 io.open**
（fengari 的 io 库是残的，io.open 是 nil）—— 所有外部输入都由 runner 注入。
--]]--

local Report = dofile(REPORT_PATH or "plugin/koreader-dex.koplugin/report.lua")

-- 跨语言共用的 fixture：日期:分钟，逗号分隔，日期倒序
local FIXTURE = (DAYS_FIXTURE or "2026-11-21:25,2026-11-20:50"):match("^%s*(.-)%s*$")

local passed, failed = 0, 0
local failures = {}
local case_names = {}

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
        print("PASS  " .. name)
    else
        failed = failed + 1
        failures[#failures + 1] = { name = name, err = tostring(err) }
        print("FAIL  " .. name .. " :: " .. tostring(err))
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
--- @param wire string 形如 "2026-11-21:25,2026-11-20:50"
--- @return string JSON
local function probe(wire)
    wire = tostring(wire or ""):match("^%s*(.-)%s*$")
    local days = Report.decode_days(wire)
    local encoded = Report.encode_days(days)
    local per = tonumber(KOREADER_PER) or 15
    return "{" ..
        '"encoded":' .. json_str(encoded) .. "," ..
        '"decoded":' .. json_days(days) .. "," ..
        '"short_url":' .. json_str(Report.short_url(days)) .. "," ..
        '"credits":' .. tostring(Report.credits(days, per)) ..
        "}"
end

return {
    passed = passed,
    failed = failed,
    failures = failures,
    cases = case_names,
    probe = probe,
}
