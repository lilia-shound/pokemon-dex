--[[--
训练家图鉴 · KOReader 插件主体

三个入口：
  1. 汇报阅读时长（最近 7 天）—— 读库 → 弹二维码
  2. 只汇报今天
  3. 自检 —— 最近 30 天的表 + 口径说明（**对账用**）

**分工**：凡是能脱离 KOReader 单独算的逻辑都放在 koreader_dex_report.lua
（那边能在沙箱里用 fengari 跑单元测试）；这个文件只负责「跟 KOReader 打交道」的部分：
读数据库、弹窗、弹二维码。

**口径**：查询用的是 koreader_dex_report.lua 里那条与 KOReader 内置 statistics
**逐字一致**的 DAILY_SQL（按设备本地时区分天）；分钟向下取整。
**二维码里放的是落地页短链**（不是 GitHub 长链 —— Kindle 上没法登录 GitHub）。
--]]--

local _ = require("gettext")

local DataStorage = require("datastorage")
local Device = require("device")
local InfoMessage = require("ui/widget/infomessage")
local QRMessage = require("ui/widget/qrmessage")
local SQ3 = require("lua-ljsqlite3/init")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")

local Screen = Device.screen

-- 同目录下的纯逻辑模块（插件加载时 KOReader 会把插件目录加进 package.path）
--
-- **名字必须全局唯一**：KOReader 把所有插件目录都加进 package.path，
-- `require("report")` 这种通用名会被别的插件的同名文件抢走 ⇒ 静默换成别人的模块。
local Report = require("koreader_dex_report")

-- 一次查多少天的数据：要报 7 天、自检 30 天 ⇒ 31 天富余
local QUERY_DAYS = 31
-- 汇报窗口：最近 7 天
local REPORT_DAYS = 7
-- 自检窗口：最近 30 天
local SELFTEST_DAYS = 30
-- 多少分钟算 1 个额度
-- **必须与 data/sources.json 里 koreader.per 一致** —— 服务端折算用的是注册表
-- 里的值，两边不一致 ⇒ 对不上账。（tests/test_koreader_plugin_syntax.py 盯着这条。）
local CREDITS_PER_MINUTES = 15

-- 说人话的提示（**这些措辞是她照着操作的，别随便改**）
local MSG_NO_DB = "KOReader 的统计数据库还没建 —— 先读一会儿书，或打开一次 Reading statistics"
local MSG_OLD_DB = "先在 KOReader 里打开一次 Reading statistics，让它把数据库升级"
local MSG_DISABLED = "内置的 Reading statistics 没打开 —— 先在 KOReader 的「设置 → Reading statistics」里打开它，不然读到的都是旧数据"
local MSG_NO_DATA = "还没有阅读记录"
local MSG_QUERY_FAILED = "读不了 KOReader 的统计数据库 —— 先在 KOReader 里打开一次 Reading statistics，让它把数据库升级"
local MSG_LAG = "刚读完的那几页可能还没落库（内置 statistics 是攒着写库的：每 50 次翻页 / 关掉书 / 息屏才落库）—— 关掉书或息屏后再汇报"

------------------------------------------------------------------
-- 小工具
------------------------------------------------------------------

--- 统计数据库在哪
local function db_path()
    return DataStorage:getSettingsDir() .. "/statistics.sqlite3"
end

--- 窗口下界/上界：都在 koreader_dex_report.lua 里（**时钟由这里传进去**）
---
--- 为什么放那儿：这是**纯日历逻辑**（跨月跨年要归一），是沙箱里**唯一测得到**的部分 ——
--- 日历边界一旦算错，表现是「少一天/多一天」：不会崩，只会静默报出错的窗口，
--- 而对账时会被她误判成「口径不一致」。
---
--- @param count number 窗口天数（含两端）
--- @param now number 当前时刻（epoch 秒）
--- @return string lower, string upper
local function window(count, now)
    return Report.window_lower(now, count), Report.window_upper(now)
end

--- 把 ljsqlite3 的错误翻译成一句人话
--- @param err any 错误信息（可能是 nil）
--- @return string
local function describe_error(err)
    local text = tostring(err or "")
    -- SQLite 缺表时的原文是 "no such table: page_stat"
    -- （page_stat 视图是内置 statistics 升级时才建的）
    --
    -- **只认这一种形态**：早先用「错误文本里含 page_stat」来判，
    -- 将来任何提到 page_stat 的错误都会被说成「老库没升级」，反而指错方向。
    if text:lower():find("no such table", 1, true) then
        return MSG_OLD_DB
    end
    if text == "" then
        return MSG_QUERY_FAILED
    end
    return MSG_QUERY_FAILED .. "（" .. text .. "）"
end

--- 把链接复制到剪贴板（兜底手段：二维码扫不出来时她可以手动粘贴）
---
--- 这个 API 我们没能从上游源码里逐条核实到（也可能这台设备压根没有剪贴板）
--- ⇒ **整个过程 pcall 包住**，失败就当没这回事，
--- 绝不能让插件因为一个兜底功能崩掉。
--- @param url string
--- @return boolean 复制成功与否
local function copy_to_clipboard(url)
    return (pcall(function()
        if type(Device.hasClipboard) == "function" and not Device:hasClipboard() then
            return
        end
        Device.input.setClipboardText(url)
    end)) and true or false
end

--- 表里有几天有记录
local function count_days(days)
    local n = 0
    for _ in pairs(days or {}) do n = n + 1 end
    return n
end

--- 这些天一共多少分钟
local function sum_minutes(days)
    local total = 0
    for _, m in pairs(days or {}) do total = total + (tonumber(m) or 0) end
    return total
end

------------------------------------------------------------------
-- 插件本体
------------------------------------------------------------------

local DexPlugin = WidgetContainer:extend{
    name = "koreader-dex",
    is_doc = false,     -- 不只在阅读界面可用
}

function DexPlugin:init()
    if self.ui and self.ui.menu then
        self.ui.menu:registerToMainMenu(self)
    end
end

function DexPlugin:addToMainMenu(menu_items)
    menu_items.koreader_dex = {
        text = _("训练家图鉴"),
        sorting_hint = "more_tools",
        sub_item_table = {
            {
                text = _("汇报阅读时长（最近 7 天）"),
                callback = function() self:onReport(REPORT_DAYS) end,
            },
            {
                text = _("只汇报今天"),
                callback = function() self:onReport(1) end,
            },
            {
                text = _("自检"),
                callback = function() self:onSelfTest() end,
            },
        },
    }
end

--- 读统计数据库
---
--- @return table|nil conn:exec 的结果（「每一列一个数组」的表）；nil = 读不了
--- @return string|nil 读不了时的原因（说人话）
function DexPlugin:readStats()
    -- 1. 内置 statistics 没开 ⇒ 库里都是旧数据，报上去等于报错数据
    --
    --    **不传默认值**：上游 readSetting(key, default) 在 key 不存在时
    --    会把 default **写进设置里**（frontend/luasettings.lua：
    --    `if not self:has(key) then self.data[key] = default end`）⇒
    --    我们要是传 {is_enabled = true}，就会抢先造出一张只有 is_enabled 的残表，
    --    内置 statistics 之后读到的 min_sec / max_sec / convert_to_db 会全变 nil。
    --    表压根不存在 ⇒ 内置 statistics 从没被打开过 ⇒ 按「默认开着」处理。
    local settings = G_reader_settings:readSetting("statistics")
    if settings and settings.is_enabled == false then
        return nil, MSG_DISABLED
    end

    -- 2. 数据库文件在不在
    --    （SQ3.open 会把不存在的文件**建出来** ⇒ 先自己看一眼，
    --      否则「还没建库」会变成「老库没升级」，提示就指错方向了）
    local path = db_path()
    local ok_file, file = pcall(function() return io.open(path, "r") end)
    if not ok_file or not file then
        return nil, MSG_NO_DB
    end
    file:close()

    -- 3. 查（SQL 的格式化只有 koreader_dex_report.lua 一个入口，这里不准自己拼）
    local since = os.time() - QUERY_DAYS * 86400
    local sql = Report.daily_sql(since)
    local ok_open, conn = pcall(function() return SQ3.open(path) end)
    if not ok_open or not conn then
        return nil, describe_error(conn)
    end
    -- conn:exec 失败时官方插件判的是「返回 nil」；少数情况下它会直接抛异常
    -- ⇒ 两种都兜住，把错误信息留下来翻译成人话
    local ok, res, err = pcall(function() return conn:exec(sql) end)
    pcall(function() conn:close() end)
    if not ok then
        return nil, describe_error(res)
    end
    if res == nil then
        return nil, describe_error(err)
    end
    return res
end

--- 弹一段说明文字
---
--- @param text string
--- @param height number|nil **显式**给高度 ⇒ 长文本变成可滚动的
---   （上游 InfoMessage：height 一旦给了就用 ScrollTextWidget + 滚动条；
---    不给就是自适应高度，文字太长时它会把字号缩小）。
---   短消息不给高度（自适应），自检那种长表给一个高度（可滚动）。
--- @param monospace boolean|nil 等宽字体（列要对齐的时候用）
function DexPlugin:showMessage(text, height, monospace)
    UIManager:show(InfoMessage:new{
        text = text,
        height = height,
        monospace_font = monospace or false,
    })
end

--- 弹二维码
--- @param url string 二维码里的短链（落地页）
function DexPlugin:showQR(url)
    -- 双向文字要不要镜像（KOReader 内置插件的写法）；
    -- 这个方法不一定在所有界面都有 ⇒ pcall 兜一下，反正是默认值 false
    local invert_bidi = false
    if self.ui and self.ui.view then
        local ok, value = pcall(function()
            return self.ui.view:shouldInvertBiDiLayoutMirroring()
        end)
        if ok and value then invert_bidi = true end
    end
    UIManager:show(QRMessage:new{
        title = _("训练家图鉴 · 汇报阅读"),
        text = url,
        invert_bidi = invert_bidi,
    })
end

--- 汇报最近 count 天的阅读时长（count = 1 ⇒ 只汇报今天）
--- @param count number
function DexPlugin:onReport(count)
    local res, err = self:readStats()
    if not res then
        self:showMessage(err)
        return
    end

    -- 只保留窗口内的天（窗口与内置 statistics 的「Last week / Last month by day」
    -- 对齐），再按天取最近 count 天（0 分钟的天丢掉 —— 汇报时不值得为 0 分钟发一条）
    local lower, upper = window(count, os.time())
    local days = Report.pick_days(
        Report.filter_dates(Report.collect_days(res), lower, upper),
        count)
    if count_days(days) == 0 then
        self:showMessage(MSG_NO_DATA)
        return
    end

    local minutes = sum_minutes(days)
    local credits = Report.credits(days, CREDITS_PER_MINUTES)
    local url = Report.short_url(days)
    local copied = copy_to_clipboard(url)

    -- 先把数字说清楚，再弹二维码（她要能一眼看到「今天 25 分钟 → 折算 1 个额度」）
    local lines = {}
    if count == 1 then
        lines[#lines + 1] = string.format("今天 %d 分钟 → 折算 %d 个额度", minutes, credits)
    else
        lines[#lines + 1] = string.format(
            "最近 %d 天（%d 天有记录）共 %d 分钟 → 折算 %d 个额度",
            count, count_days(days), minutes, credits)
    end
    if copied then
        lines[#lines + 1] = "同一条链接已经复制到剪贴板了（二维码扫不出来时可以手动粘贴）"
    end
    lines[#lines + 1] = MSG_LAG

    UIManager:show(InfoMessage:new{
        text = table.concat(lines, "\n"),
        dismiss_callback = function()
            self:showQR(url)
        end,
    })
end

--- 自检：最近 30 天的表 + 口径说明（**对账用** —— 沙箱里没有真机，只能靠它对账）
function DexPlugin:onSelfTest()
    local res, err = self:readStats()
    if not res then
        self:showMessage(err)
        return
    end

    local lower, upper = window(SELFTEST_DAYS, os.time())
    local minutes_by_day = Report.filter_dates(Report.collect_days(res), lower, upper)
    local pages_by_day = Report.filter_dates(Report.collect_pages(res), lower, upper)

    -- **窗口内所有「有页数」的天都要列出来**（不足 1 分钟的天也列，分钟数显示 0）：
    -- 内置 statistics 的「Last week / Last month by day」是按 page_stat 的行分组的，
    -- 一天读了 45 秒也会显示成一行「00:00:45 (1 page)」。
    -- 我们要是把 0 分钟的天丢掉 ⇒ 她在内置那边看到一天、插件这边没有 ⇒
    -- **她会误判插件坏了** —— 而这张表就是用来对账的。
    local dates = {}
    for d in pairs(pages_by_day) do dates[#dates + 1] = d end
    table.sort(dates, function(a, b) return a > b end)

    local lines = {}
    lines[#lines + 1] = "训练家图鉴 · 自检"
    lines[#lines + 1] = ""
    lines[#lines + 1] = "数据来源：" .. db_path()
    lines[#lines + 1] = "  （KOReader 内置 statistics 插件自己记的库）"
    lines[#lines + 1] = "口径：与内置 statistics 完全同一条 SQL ——"
    lines[#lines + 1] = "  按设备本地时区分天；同一页当天读多次会累加；"
    lines[#lines + 1] = "  页数按内置的 page_stat 视图重算（reflowable 重排过也算）。"
    lines[#lines + 1] = "下面每行是：日期  分钟  页数（只列有记录的天）。"
    lines[#lines + 1] = "  分钟 = 丢掉秒数（内置那边显示 时:分:秒，这边是整分钟、向下取整）。"
    lines[#lines + 1] = ""
    if #dates == 0 then
        lines[#lines + 1] = string.format("最近 %d 天：还没有阅读记录", SELFTEST_DAYS)
    else
        lines[#lines + 1] = string.format(
            "最近 %d 天（%d 天有记录）：", SELFTEST_DAYS, #dates)
        for _, d in ipairs(dates) do
            -- 等宽字体下两列才对得齐：数字用定宽字段，每行后面的字数也一样
            lines[#lines + 1] = string.format(
                "%s  %5d 分钟  %5d 页", d, minutes_by_day[d] or 0, pages_by_day[d] or 0)
        end
        lines[#lines + 1] = string.format(
            "合计 %d 分钟 → 折算 %d 个额度",
            sum_minutes(minutes_by_day), Report.credits(minutes_by_day, CREDITS_PER_MINUTES))
    end
    lines[#lines + 1] = ""
    lines[#lines + 1] = "怎么对照：KOReader 里打开「工具 → Reading statistics →"
    lines[#lines + 1] = "Time range」，页面标题是「Time range statistics」，"
    lines[#lines + 1] = "里面有一项「Last week」（最近 7 天，逐天），"
    lines[#lines + 1] = "30 天那张叫「Last month by day」。"
    lines[#lines + 1] = "它每行是「日期 (星期几)  时长 (N 页)」，例如"
    lines[#lines + 1] = "「2026-11-19  00:00:45 (1 page)」。"
    lines[#lines + 1] = "对照：「Last week」是最近 7 天、「Last month by day」是最近 30 天，"
    lines[#lines + 1] = "它们也只列有记录的天 ⇒ 跟上面这张表里同一个日子的那行比："
    lines[#lines + 1] = "分钟数要一样、页数要一样。口径一样 ⇒ 必须一模一样。"
    lines[#lines + 1] = ""
    lines[#lines + 1] = MSG_LAG

    -- 等宽字体：默认的比例字体下两个空格分列对不齐（对账要看的正是这两列）；
    -- 给了 height 就是可滚动的（上游 InfoMessage：给了 height 用 ScrollTextWidget）。
    self:showMessage(table.concat(lines, "\n"), Screen:scaleBySize(400), true)
end

return DexPlugin
