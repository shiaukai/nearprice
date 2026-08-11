#!/usr/bin/env bash
# 打包成 claude.ai 可上傳的 zip（Settings → Features → Skills）。
#
# 只放進 skill 需要的檔案，排除 .git、金鑰、輸出與 README/LICENSE。
# 注意：claude.ai 的沙箱預設沒有本工具需要的網域白名單，上傳後多半還要
# 請管理員開放對外連線，詳見 README 的「能裝在 claude.ai / Cowork 嗎？」。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$PWD/nearprice-skill.zip}"

cd "$ROOT"
rm -f "$OUT"
zip -q -r "$OUT" \
  SKILL.md scripts references config.example.json .env.example \
  -x '*/__pycache__/*' '*.pyc' 'scripts/package.sh'

echo "→ $OUT"
unzip -l "$OUT" | tail -n +4 | head -20
