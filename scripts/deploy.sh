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
cp data/pokedex.json site/pokedex.json
cp data/dex.json site/dex.json
# 源注册表也要跟着走 —— 读方（图鉴页 / 插件）拿它兜底「掉一只要读多久」，
# S5 的多源进度也走它（workflow 里也 cp 同一份）
cp data/sources.json site/sources.json
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
