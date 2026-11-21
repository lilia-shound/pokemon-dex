--[[--
纯逻辑：拼短链、格式化天数、SQL 常量、日历窗口。

**刻意不 require 任何 KOReader 模块** —— 这样这一整个文件能在沙箱里
（用 fengari 这个 Lua VM）跑单元测试，不必等上真机才发现拼错了 URL。

KOReader 插件里凡是「能脱离 KOReader 单独算」的部分，都放这里。

**文件名为什么带前缀**：KOReader 会把**所有**插件目录都加进 package.path
（上游 frontend/pluginloader.lua：`package.path = package.path .. ";" .. plugin.path .. "/?.lua"`），
所以 `require("report")` 这种**太通用的名字**会被别的插件的同名文件抢走
（谁先被加进 package.path 谁赢）⇒ 我们的模块会被静默替换成别人的。
故取一个全局唯一的名字。
--]]--

local Report = {}

-- 手机落地页的基址：二维码里放的是这条短链，不是 GitHub 的长链接
-- （Kindle 上没法登录 GitHub，扫了长链也提交不了）
Report.BASE_URL = "https://lilia-shound.github.io/pokemon-dex/koreader/?days="

-- 报告里的源 id（服务端注册表里要同名）
Report.SOURCE_ID = "koreader"

-- 服务端的单日上限：一天最多 1440 分钟（= 24 × 60）。
-- 口径来源：data/sources.json → sources.koreader.maxPerDay；
-- 服务端 scripts/sources.py 的 credit_for_day 会 min(原始量, maxPerDay) 再折算额度。
-- 插件这边也照着夹一下 —— 「插件算出来的额度」与「服务端算出来的额度」必须一致。
-- （tests/test_koreader_lua.py 里有条用例盯着这个常量跟注册表不漂移。）
Report.MAX_MINUTES_PER_DAY = 1440

-- 与 KOReader 内置 statistics 的 sqlDaily() **逐字一致**的按天汇总口径。
--
-- 抄它的三个要点：
--   1. 查 `page_stat` 视图而不是 `page_stat_data` —— 视图会按当前页数重算
--      reflowable 文档的分页（内置插件自己就是这么算的）
--   2. GROUP BY id_book, page, dates —— 同一页当天读多次要累加
--   3. strftime(...,'localtime') —— 按**设备本地时区**分天
--      （她的设备在台州 = UTC+8，和服务端的北京时间对得上 ✓）
--
-- 口径不一样的话，她拿插件的数字去对 KOReader 自带的「Last week by day」
-- 就会对不上 —— 而对账是这整个子项目的验收手段。
Report.DAILY_SQL = [[
            SELECT dates,
                   count(*)             AS pages,
                   sum(sum_duration)    AS durations,
                   start_time
            FROM   (
                        SELECT strftime('%%Y-%%m-%%d', start_time, 'unixepoch', 'localtime') AS dates,
                               sum(duration)                                                 AS sum_duration,
                               start_time
                        FROM   page_stat
                        WHERE  start_time >= %d
                        GROUP  BY id_book, page, dates
                   )
            GROUP  BY dates
            ORDER  BY dates DESC;
    ]]

--- 把 DAILY_SQL 里的占位符填上，产出**设备上真正执行的那条 SQL**。
--
-- **这是格式化 SQL 的唯一入口** —— 插件主体（main.lua）只准调这个函数，
-- 不准自己拼 SQL、不准自己调 string.format。
--
-- 为什么非要收口：
--   这条模板里有两个不同的百分号用法 —— `%d`（要填的 epoch）与 `%%`（转义成字面的 %）。
--   `%%Y-%%m-%%d` 只有经过 `string.format` 才会变成 `%Y-%m-%d`；
--   万一将来有人改用**绑定参数**（对 `%d` 来说是很自然的选择），
--   `%%` 就会原样留在 SQL 里，SQLite 的 strftime 会把 `%%` 渲染成字面的 `%`，
--   于是每一行日期都变成 "%Y-%m-%d"，插件把分钟全汇总到一个假的日子上 ——
--   **静默产出错数据，只有真机对账才暴露**。
--   收口 + 有测试盯着 ⇒ 这种改动当场就红。
--
-- @param since number epoch 秒（起始时间下限）
-- @return string 可以直接丢给 conn:prepare() 的 SQL
function Report.daily_sql(since)
    -- math.floor：string.format 的 %d 不接受带小数的数，先取整免得运行期炸
    return string.format(Report.DAILY_SQL, math.floor(tonumber(since) or 0))
end

--- 把 {["2026-11-21"] = 25} 变成 "2026-11-21:25,2026-11-20:50"（日期倒序）
-- @param days table<string, number>
-- @return string
function Report.encode_days(days)
    local keys = {}
    for d in pairs(days or {}) do
        keys[#keys + 1] = d
    end
    -- 倒序：最新的一天在最前面
    table.sort(keys, function(a, b) return a > b end)
    local parts = {}
    for i = 1, #keys do
        local d = keys[i]
        parts[#parts + 1] = d .. ":" .. tostring(math.floor(tonumber(days[d]) or 0))
    end
    return table.concat(parts, ",")
end

--- encode_days 的逆操作（手机落地页那一侧也要能解）
--
-- **格式规则（唯一真值来源，Python / JS 两侧照抄）**：
--   - 一段就是 `YYYY-MM-DD:MM`，逗号分隔；
--   - **任何一段只要不完全匹配就整段丢掉** —— 不 trim、不做别的容错；
--   - 数字只认 ASCII 0-9（Lua 的 %d 本来就是 ASCII-only ✓）。
--     "2026-11-21:25, 2026-11-20:50" ⇒ 只剩 21 号那一段（带空白的段整段丢掉）。
--
-- @param str string
-- @return table<string, number>
function Report.decode_days(str)
    local days = {}
    for chunk in tostring(str or ""):gmatch("[^,]+") do
        local d, m = chunk:match("^(%d%d%d%d%-%d%d%-%d%d):(%d+)$")
        if d then
            days[d] = math.floor(tonumber(m) or 0)
        end
    end
    return days
end

--- 拼二维码里那条短链
-- @param days table<string, number>
-- @return string
function Report.short_url(days)
    return Report.BASE_URL .. Report.encode_days(days)
end

--- 秒 → 分钟（向下取整，跟服务端的 floor 口径一致）
-- @param seconds number
-- @return number
function Report.to_minutes(seconds)
    return math.floor((tonumber(seconds) or 0) / 60)
end

--- 把 ljsqlite3 的查询结果整理成 {日期 = 分钟}
---
--- **查询结果的形状要记牢**：ljsqlite3 的 `conn:exec(sql)` 返回的是
--- **「每一列一个数组」的表**，不是行数组：
---     res[1][i] = 日期字符串（DAILY_SQL 的第 1 列）
---     res[2][i] = 页数（第 2 列）
---     res[3][i] = 时长秒数（第 3 列）
---     res[4][i] = 起始时间戳（第 4 列）
--- （列号比列名稳 —— 用列名的话，SQL 里改了别名这里就悄悄空了。）
---
--- 这个函数是**纯的**（不碰 DB、不碰 KOReader）⇒ 能在沙箱里用 fengari 单测 ✓
---
--- **0 分钟的天会留着**（秒数不够一分钟就是 0）——
--- 要不要留是「要不要报」的问题，交给 `Report.pick_days` 决定 ✓
---
--- @param res table|nil conn:exec() 的返回值
--- @return table<string, number> {["2026-11-21"] = 25}（分钟，向下取整）
function Report.collect_days(res)
    local days = {}
    if not res then return days end
    local dates = res[1]
    local durations = res[3]
    if not dates then return days end
    for i = 1, #dates do
        local date = dates[i]
        if date then
            days[tostring(date)] = Report.to_minutes(durations and durations[i] or 0)
        end
    end
    return days
end

--- 把 ljsqlite3 的查询结果整理成 {日期 = 页数}
---
--- 与 `Report.collect_days` 读的是**同一个结果表**（第 2 列）——
--- 自检那张表要显示页数（跟内置 statistics 的页数逐天对照）✓
---
--- @param res table|nil conn:exec() 的返回值
--- @return table<string, number> {["2026-11-21"] = 3}（页）
function Report.collect_pages(res)
    local pages = {}
    if not res then return pages end
    local dates = res[1]
    local counts = res[2]
    if not dates then return pages end
    for i = 1, #dates do
        local date = dates[i]
        if date then
            pages[tostring(date)] = math.floor(tonumber(counts and counts[i] or 0) or 0)
        end
    end
    return pages
end

--- 只留下日期落在 [lower, upper] 里的天
---
--- 日期是 "YYYY-MM-DD"（SQLite 的 strftime('%Y-%m-%d') 出来的，零填充 ✓）
--- ⇒ **字符串比大小就是比日期先后** ✓
---
--- 为什么要它：内置 statistics 的「Last week by day」用的是**本地零点**窗口，
--- 我们的查询窗口是滚动窗口 ⇒ 两边可能差一天。
--- 按日历日切一刀，两边的「是哪几天」就严格一致了（对账用）✓
---
--- @param days table<string, number>
--- @param lower string|nil 起始日期（含）；nil = 不限
--- @param upper string|nil 结束日期（含）；nil = 不限
--- @return table<string, number>
function Report.filter_dates(days, lower, upper)
    local out = {}
    for d, m in pairs(days or {}) do
        if (not lower or d >= lower) and (not upper or d <= upper) then
            out[d] = m
        end
    end
    return out
end

--- 取最近 max_days 天（有分钟数的才要），日期倒序
-- @param all_days table<string, number>
-- @param max_days number|nil 上限（认不出来的数就当「没有上限」）
-- @return table<string, number>
function Report.pick_days(all_days, max_days)
    local keys = {}
    for d, m in pairs(all_days or {}) do
        if (tonumber(m) or 0) > 0 then
            keys[#keys + 1] = d
        end
    end
    table.sort(keys, function(a, b) return a > b end)
    -- tonumber 兜一下：Lua 5.3 的 math.min 不接受字符串，
    -- 上限传成 "3" 或 "abc" 时不该当场抛错（认不出来就当没有上限）。
    local limit = #keys
    local n = tonumber(max_days)
    if n then
        limit = math.min(#keys, math.floor(n))
    end
    local out = {}
    for i = 1, limit do
        out[keys[i]] = all_days[keys[i]]
    end
    return out
end

--- 这些分钟一共折算多少个额度（每个源多少分钟算 1 个）
--
-- 口径与服务端 scripts/sources.py 的 credit_for_day 一致：
--   1. 每天的原始量先夹到 maxPerDay（没有上限的源不夹）；
--   2. 负数夹到 0；
--   3. **按天**向下取整（不跨天凑零头）。
--
-- @param days table<string, number>
-- @param per number
-- @param max_per_day number|nil 单日上限（分钟），默认 Report.MAX_MINUTES_PER_DAY
-- @return number
function Report.credits(days, per, max_per_day)
    local p = tonumber(per) or 1
    if p < 1 then p = 1 end
    local cap = tonumber(max_per_day)
    if not cap then cap = Report.MAX_MINUTES_PER_DAY end
    local total = 0
    for _, m in pairs(days or {}) do
        local amount = tonumber(m) or 0
        if amount > cap then amount = cap end
        if amount < 0 then amount = 0 end
        total = total + math.floor(amount / p)
    end
    return total
end

------------------------------------------------------------------
-- 日历窗口：最近 N 天（含今天）
--
-- **时钟是参数**（now_stamp）：调用方自己传 os.time()。
-- 这样这段纯日历逻辑能脱离 KOReader 在沙箱里测（fengari）——
-- 日历边界一旦算错，表现是「少一天/多一天」：不会崩，
-- 只会静默报出错的窗口 ⇒ 对账时被她误判成「口径不一致」。
------------------------------------------------------------------

-- 每个月的天数（2 月按闰年算）
local MONTH_DAYS = { 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 }

--- 闰年判定（公历：四年一闰，百年不闰，四百年再闰）
--- @param year number
--- @return boolean
local function is_leap_year(year)
    local y = math.floor(tonumber(year) or 0)
    return (y % 4 == 0 and y % 100 ~= 0) or (y % 400 == 0)
end

--- 某年某月有几天
--- @param year number
--- @param month number 1..12
--- @return number
local function days_in_month(year, month)
    if month == 2 and is_leap_year(year) then
        return 29
    end
    return MONTH_DAYS[month] or 0
end

--- 两位数补零（月份/日期）
---
--- **不用 string.format**：report.lua 里只准有 Report.daily_sql 一处 string.format
--- （SQL 的格式化要收口在一个入口，有测试盯着）。
--- @param n number
--- @return string
local function pad2(n)
    local i = math.floor(tonumber(n) or 0)
    if i < 10 then
        return "0" .. tostring(i)
    end
    return tostring(i)
end

--- 年月日 → "YYYY-MM-DD"
--- @param year number
--- @param month number
--- @param day number
--- @return string
local function format_date(year, month, day)
    return tostring(math.floor(tonumber(year) or 0)) .. "-" .. pad2(month) .. "-" .. pad2(day)
end

--- 把 "YYYY-MM-DD" 加减若干天（**纯日历算术**，不碰时区）
---
--- 为什么自己算，而不用 `os.time{day = 现在的日 - N}` 让 mktime 去归一：
---   - os.time 走 mktime，跨月跨年能不能归一**取决于操作系统**；
---     而且本地午夜正好落在夏令时切换的那个小时里时，mktime 会返回 -1
---     （Lua 的 os.time 直接抛错）⇒ 插件会在真机上炸。
---   - 这里纯算术 ⇒ 结果只跟日历有关，与时区、夏令时、操作系统都无关
---     ⇒ 沙箱里能逐个边界测（跨月、跨年、闰年）。
---
--- @param date_str string "YYYY-MM-DD"
--- @param delta number 要加减的天数（正数往后，负数往前）
--- @return string "YYYY-MM-DD"（输入不是合法日期就原样返回）
function Report.date_shift(date_str, delta)
    local y, m, d = tostring(date_str or ""):match("^(%d%d%d%d)%-(%d%d)%-(%d%d)$")
    if not y then
        return date_str
    end
    y, m, d = math.floor(tonumber(y)), math.floor(tonumber(m)), math.floor(tonumber(d))
    local step = math.floor(tonumber(delta) or 0)
    while step > 0 do
        d = d + 1
        if d > days_in_month(y, m) then
            d = 1
            m = m + 1
            if m > 12 then
                m = 1
                y = y + 1
            end
        end
        step = step - 1
    end
    while step < 0 do
        d = d - 1
        if d < 1 then
            m = m - 1
            if m < 1 then
                m = 12
                y = y - 1
            end
            d = days_in_month(y, m)
        end
        step = step + 1
    end
    return format_date(y, m, d)
end

--- 时间戳 → **本地**日历日（"YYYY-MM-DD"）
---
--- 本地（不是 UTC）：内置 statistics 就是按**设备本地时区**分天的
--- （上游 sqlDaily() 里的 strftime(..., 'localtime')）⇒ 两边必须一样。
---
--- @param now_stamp number 当前时刻（epoch 秒）；给不了就用系统当前时间
--- @return string "YYYY-MM-DD"
function Report.local_date(now_stamp)
    local stamp = tonumber(now_stamp)
    if not stamp then
        stamp = os.time()
    end
    return os.date("%Y-%m-%d", stamp)
end

--- 窗口下界：今天往前推 count-1 天的**本地日历日**（"YYYY-MM-DD"）
---
--- 为什么不用「现在往前推 count 天」那种滚动窗口：
--- 内置 statistics 的「Last week / Last month by day」用的就是**本地零点**窗口
--- （上游 getDatesFromAll：period_begin = now_stamp - (sdays-1)*86400 - 当天已过的秒数），
--- 我们跟它一模一样 ⇒ 两边的「是哪几天」严格相同（对账时不会多出一天、少一天）。
---
--- @param now_stamp number 当前时刻（epoch 秒）—— **时钟由调用方传进来**
--- @param count number 窗口天数（含两端）
--- @return string "YYYY-MM-DD"
function Report.window_lower(now_stamp, count)
    local n = math.floor(tonumber(count) or 1)
    if n < 1 then
        n = 1
    end
    return Report.date_shift(Report.local_date(now_stamp), -(n - 1))
end

--- 窗口上界：今天（本地日历日，"YYYY-MM-DD"）
--- @param now_stamp number 当前时刻（epoch 秒）—— **时钟由调用方传进来**
--- @return string "YYYY-MM-DD"
function Report.window_upper(now_stamp)
    return Report.local_date(now_stamp)
end

return Report
