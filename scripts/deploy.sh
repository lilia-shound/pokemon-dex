#!/usr/bin/env bash
# 部署：把图鉴站推到 GitHub Pages
#   main 分支    —— 源码 + 数据（site/ 里含图片与静态文件）
#   gh-pages 分支 —— 只含 site/ 的内容，作为 Pages 源
# git 内容寻址，同一张图片 blob 不会存两份。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REPO_URL="https://github.com/lilia-shound/pokemon-dex.git"

echo "[1/4] 同步数据到 site/"
cp data/pokedex.json site/pokedex.json
cp data/dex.json site/dex.json
touch site/.nojekyll

echo "[2/4] 提交并推送 main"
git add -A
if git diff --cached --quiet; then
  echo "      无改动，跳过提交"
else
  git -c user.name="Mavis" -c user.email="mavis@local" commit -q -m "chore: 更新图鉴站与状态"
fi
if [ -n "${GITHUB_TOKEN:-}" ]; then
  AUTH=$(printf 'x-access-token:%s' "$GITHUB_TOKEN")
else
  AUTH=""
fi
if [ -n "$AUTH" ]; then
  git push "https://${AUTH}@github.com/lilia-shound/pokemon-dex.git" HEAD:main
else
  git push origin HEAD:main
fi

echo "[3/4] 推送 gh-pages 分支"
if [ -n "$AUTH" ]; then
  git subtree push --prefix site "https://${AUTH}@github.com/lilia-shound/pokemon-dex.git" gh-pages
else
  git subtree push --prefix site origin gh-pages
fi

echo "[4/4] 完成"
echo "图鉴页： https://lilia-shound.github.io/pokemon-dex/"
