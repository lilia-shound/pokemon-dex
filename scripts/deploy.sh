#!/usr/bin/env bash
# 部署：把图鉴站推到 GitHub Pages
#   main 分支     —— 源码 + 数据 + site/ 里的静态文件
#   gh-pages 分支 —— 只含 site/ 的内容，作为 Pages 源
#
# gh-pages 用「独立 worktree + orphan 分支 + force push」重建。
# 不用 git subtree 的原因：
#   1) git 2.39 的 subtree 没有 --force，main 一旦 force push 就会非 fast-forward 推不上去
#   2) subtree 的历史会把 main 的历史带进 Pages 分支，越来越臃肿
#
# token 通过 GIT_ASKPASS 传入，绝不出现在命令行参数或 git 配置里。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REPO="lilia-shound/pokemon-dex"
PUSH_URL="https://github.com/${REPO}.git"

echo "[1/4] 同步数据到 site/"
# 图鉴页用的 pokedex.json 是瘦身版（去掉运行时用不到的字段，1.6MB → 280KB）
python3 scripts/build_site_pokedex.py
cp data/dex.json site/dex.json
# 源注册表也要跟着走 —— 读方（图鉴页 / 插件）拿它兜底「掉一只要读多久」，
# S5 的多源进度也走它（workflow 里也 cp 同一份）
cp data/sources.json site/sources.json
# 番茄钟用的精简宝可梦清单（从 pokedex.json 生成，1.8MB → 104KB）
python3 scripts/build_pokedex_lite.py
touch site/.nojekyll

echo "[2/4] 提交并推送 main"
git add -A
if git diff --cached --quiet; then
  echo "      无改动，跳过提交"
else
  git -c user.name="Mavis" -c user.email="mavis@local" commit -q -m "chore: 更新图鉴站与状态"
fi

if [ -n "${GITHUB_TOKEN:-}" ]; then
  ASKPASS="$(mktemp)"
  cat > "$ASKPASS" <<'EOF'
#!/bin/sh
case "$1" in
  *sername*) echo "x-access-token" ;;
  *assword*) echo "$GITHUB_TOKEN" ;;
  *) echo "" ;;
esac
EOF
  chmod +x "$ASKPASS"
  export GIT_ASKPASS="$ASKPASS"
  export GIT_TERMINAL_PROMPT=0
fi

for attempt in 1 2 3 4 5; do
  if git push "$PUSH_URL" HEAD:main; then break; fi
  echo "      第 ${attempt} 次推送失败，重试…"
  sleep 7
  [ "$attempt" = "5" ] && { echo "[失败] main 推送失败"; exit 1; }
done

echo "[3/4] 重建并推送 gh-pages"
WT="$(mktemp -d)"
cleanup() {
  git worktree remove --force "$WT" >/dev/null 2>&1 || true
  rm -rf "$WT"
  [ -n "${ASKPASS:-}" ] && rm -f "$ASKPASS"
}
trap cleanup EXIT

# 上一次部署会在本地留下 orphan 分支 ghp。沙箱是持久的，同一个 clone 里第二次部署时
# `git checkout --orphan ghp` 会因为分支已存在而失败 —— 而且原来那行是 >/dev/null 2>&1，
# 报错被吞掉，表现就是「main 推了、gh-pages 没推」。这里先清掉旧分支。
git worktree prune
git branch -D ghp >/dev/null 2>&1 || true

git worktree add --detach "$WT" >/dev/null
(
  cd "$WT"
  git checkout --orphan ghp
  git rm -rf --cached . -q >/dev/null 2>&1 || true
  find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} + 2>/dev/null || true
  cp -r "$ROOT/site/." .
  # 油猴脚本也发布到线上：这样插件有稳定安装地址，
  # 加上 @updateURL/@downloadURL 后 Tampermonkey 能自动更新，不用每次手动换文件。
  cp "$ROOT/plugin/weread-dex.user.js" ./weread-dex.user.js
  # KOReader 插件目录也发布出去 —— 不然她没地方拿插件文件（插件不能像油猴脚本那样
  # 一个文件装完，得整个目录丢进设备的 plugins/）。
  # 发的是**整个目录**：
  #   https://lilia-shound.github.io/pokemon-dex/koreader-dex.koplugin/
  #   https://lilia-shound.github.io/pokemon-dex/koreader-dex.koplugin/main.lua
  # 目录名必须是 koreader-dex.koplugin（KOReader 靠它认出插件）⇒ 别改名。
  rm -rf ./koreader-dex.koplugin
  cp -r "$ROOT/plugin/koreader-dex.koplugin" ./koreader-dex.koplugin
  # 目录 URL 也要能打开 ⇒ 给那个目录写一张索引页。
  # GitHub Pages 不会给**没有 index.html 的目录**生成索引页 ⇒ 只发目录的话，
  # 她点「线上目录」那个链接就是 404 ⇒ 插件根本下不到。
  # 注意：index.html 是**部署产物** —— 只写进 gh-pages 的发布树，
  # 不往仓库里的 plugin/koreader-dex.koplugin/ 塞（那个目录是插件本体，KOReader 会读它）。
  # 里面的文件直链用**相对路径**（同一个目录里 ⇒ 域名换掉也不用改）。
  cat > ./koreader-dex.koplugin/index.html <<'PLUGIN_INDEX_HTML'
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>训练家图鉴 · KOReader 插件</title>
<style>
body { font-family: system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
       max-width: 40rem; margin: 0 auto; padding: 2rem 1.25rem; line-height: 1.7;
       color: #171310; background: #f6f1e7; }
h1 { font-size: 1.4rem; margin: 0 0 .6rem; }
p.sub { color: #6b5b4a; margin: 0 0 1.5rem; }
ul { list-style: none; padding: 0; margin: 0; }
li { background: #fff; border: 1px solid #e3d8c6; border-radius: 10px;
     padding: .7rem 1rem; margin-bottom: .6rem; }
a { color: #c8623a; font-weight: 600; text-decoration: none; }
a:hover { text-decoration: underline; }
code { background: #efe7d8; border-radius: 4px; padding: 0 .3em; }
.note { color: #6b5b4a; font-size: .92rem; margin-top: 1.5rem; }
</style>
</head>
<body>
<h1>训练家图鉴 · KOReader 插件</h1>
<p class="sub">这一页是<b>索引</b>：下面是插件目录里的 4 个文件。下载后放进一个叫
<code>koreader-dex.koplugin</code> 的文件夹（<b>目录名不能改</b>，KOReader 靠它认出插件），
再把整个文件夹拷进设备的 <code>plugins/</code> 目录，然后重启 KOReader。</p>
<ul>
  <li><a href="main.lua">main.lua</a> —— 插件主体（读库、弹窗、弹二维码）</li>
  <li><a href="koreader_dex_report.lua">koreader_dex_report.lua</a> —— 纯计算部分（拼链接、算额度、算日历窗口）</li>
  <li><a href="_meta.lua">_meta.lua</a> —— 插件的名字和简介</li>
  <li><a href="README.md">README.md</a> —— 插件自己的说明书</li>
</ul>
<p class="note">完整的手把手操作 + 对账步骤见仓库里的 <code>docs/koreader-setup.md</code>；
插件文件在仓库里的 <code>plugin/koreader-dex.koplugin/</code>（同一个 clone 里，不用下载也行）。</p>
</body>
</html>
PLUGIN_INDEX_HTML
  git add -A
  git -c user.name="Mavis" -c user.email="mavis@local" commit -q -m "chore: 图鉴站 $(date -u +%FT%TZ)"
)
for attempt in 1 2 3 4 5; do
  if (cd "$WT" && git push --force "$PUSH_URL" HEAD:gh-pages); then break; fi
  echo "      第 ${attempt} 次推送失败，重试…"
  sleep 7
  [ "$attempt" = "5" ] && { echo "[失败] gh-pages 推送失败"; exit 1; }
done

echo "[4/4] 完成"
echo "图鉴页： https://lilia-shound.github.io/pokemon-dex/"
echo "KOReader 插件： https://lilia-shound.github.io/pokemon-dex/koreader-dex.koplugin/"
echo "  （上面是索引页；文件直链在该页最下面，例："
echo "   https://lilia-shound.github.io/pokemon-dex/koreader-dex.koplugin/main.lua）"
