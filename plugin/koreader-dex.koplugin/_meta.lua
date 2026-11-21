--[[--
训练家图鉴 · KOReader 插件的元信息

安装：把 koreader-dex.koplugin/ 整个目录复制到设备的 plugins/
（Kindle：/mnt/us/koreader/plugins/）→ 重启 KOReader ✓
--]]--

local _ = require("gettext")

return {
    fullname = _("训练家图鉴"),
    description = _([[汇报阅读时长到训练家图鉴：用二维码把最近几天的阅读时长带回手机。]]),
}
