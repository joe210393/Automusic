#!/bin/zsh
# 把 Automusic 相關服務裝成「登入即啟動、掛了會重開」
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UID_NUM="$(id -u)"
AGENTS="$HOME/Library/LaunchAgents"

mkdir -p "$AGENTS"

for name in com.automusic.server com.automusic.ngrok; do
  src="$ROOT/launchd/${name}.plist"
  dst="$AGENTS/${name}.plist"
  # 依目前使用者路徑改寫（避免換電腦後路徑錯）
  sed -e "s|/Users/hung-weichen|$HOME|g" "$src" > "$dst"
  echo "installed $dst"

  # 先卸下（已載入時 bootstrap 會失敗）
  launchctl bootout "gui/$UID_NUM/$name" 2>/dev/null || true
  launchctl unload "$dst" 2>/dev/null || true
  sleep 0.3
  # load -w：寫入登入 session，開機／登入會自動跑
  launchctl load -w "$dst"
  launchctl kickstart -k "gui/$UID_NUM/$name" 2>/dev/null || true
done

echo ""
echo "已設定開機／登入自動啟動。等 3 秒做健康檢查…"
sleep 3
"$ROOT/scripts/automusic-healthcheck.sh" || true
