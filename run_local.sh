#!/bin/bash
# run_local.sh — 本機執行模式
#
# 用途：當 591 封鎖 GitHub Actions IP 時，改在本機執行爬蟲。
# 執行後會自動 commit 並 push 更新的地圖資料到 GitHub。
#
# 使用方式：
#   chmod +x run_local.sh
#   ./run_local.sh

set -e  # 任何指令失敗即停止

# ── 確認 .env 存在 ──
if [ ! -f ".env" ]; then
  echo "❌ 找不到 .env 檔案！"
  echo "   請先執行：cp .env.example .env 並填入 API 金鑰"
  exit 1
fi

# ── 載入環境變數 ──
export $(grep -v '^#' .env | grep -v '^$' | xargs)

# ── 強制使用本機模式（非 headless，減少被偵測機率）──
export RUN_MODE=local

echo "🏠 租屋偵探 本機模式啟動"
echo "   時間：$(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# ── 執行爬蟲 ──
python main.py

echo ""
echo "📁 準備提交地圖資料..."

# ── 自動 commit 並 push（若有變更）──
git add data/listings.json docs/map.html

if git diff --staged --quiet; then
  echo "ℹ️  無新資料，不需要提交"
else
  COMMIT_MSG="chore: 本機手動更新 $(date '+%Y-%m-%d %H:%M')"
  git commit -m "$COMMIT_MSG"
  git push
  echo "✅ 已推送更新：$COMMIT_MSG"
fi

echo ""
echo "✅ 本機執行完畢"
