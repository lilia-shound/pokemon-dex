--[[--
纯逻辑：拼短链、格式化天数、SQL 常量。

**刻意不 require 任何 KOReader 模块** —— 这样这一整个文件能在沙箱里
（用 fengari 这个 Lua VM）跑单元测试，不必等上真机才发现拼错了 URL。

KOReader 插件里凡是「能脱离 KOReader 单独算」的部分，都放这里。
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

return Report
