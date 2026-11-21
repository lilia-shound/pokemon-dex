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
-- @param max_days number|nil
-- @return table<string, number>
function Report.pick_days(all_days, max_days)
    local keys = {}
    for d, m in pairs(all_days or {}) do
        if (tonumber(m) or 0) > 0 then
            keys[#keys + 1] = d
        end
    end
    table.sort(keys, function(a, b) return a > b end)
    local limit = max_days and math.min(#keys, max_days) or #keys
    local out = {}
    for i = 1, limit do
        out[keys[i]] = all_days[keys[i]]
    end
    return out
end

--- 这些分钟一共折算多少个额度（每个源多少分钟算 1 个）
-- @param days table<string, number>
-- @param per number
-- @return number
function Report.credits(days, per)
    local p = tonumber(per) or 1
    if p < 1 then p = 1 end
    local total = 0
    for _, m in pairs(days or {}) do
        total = total + math.floor((tonumber(m) or 0) / p)
    end
    return total
end

return Report
