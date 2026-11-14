#!/usr/bin/env bash
# 部署：把图鉴站推到 GitHub Pages
#   main 分支     —— 源码 + 数据（site/ 里含静态文件）
#   gh-pages 分支 —— 只含 site/ 的内容，作为 Pages 源
#
# token 通过 GIT_ASKPASS 传入，绝不出现在命令行参数或 git 配置里。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REPO="lilia-shound/pokemon-dex"

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
  PUSH_URL="https://github.com/${REPO}.git"
else
  PUSH_URL="origin"
fi

git push "$PUSH_URL" HEAD:main

echo "[3/4] 推送 gh-pages 分支"
git subtree push --prefix site "$PUSH_URL" gh-pages

[ -n "${ASKPASS:-}" ] && rm -f "$ASKPASS"

echo "[4/4] 完成"
echo "图鉴页： https://${REPO%/*}/$(echo "$REPO" | cut -d/ -f2)/"
