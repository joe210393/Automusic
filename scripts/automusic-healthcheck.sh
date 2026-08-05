#!/bin/zsh
# Automusic 開機就緒檢查（本機）
set -u
ok=0
fail=0

check() {
  local name="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "OK  $name"
    ok=$((ok + 1))
  else
    echo "NG  $name"
    fail=$((fail + 1))
  fi
}

echo "=== Automusic health ==="
check "LaunchAgent server loaded" launchctl print "gui/$(id -u)/com.automusic.server"
check "LaunchAgent ngrok loaded" launchctl print "gui/$(id -u)/com.automusic.ngrok"
check "FastAPI :8080" curl -sf -o /dev/null -w '' http://127.0.0.1:8080/docs
check "Health API" curl -sf http://127.0.0.1:8080/health
check "ngrok local API" curl -sf http://127.0.0.1:4040/api/tunnels
check "DiffSinger dir" test -f "$HOME/diffsinger/infer_cli.py"
check "DiffSinger venv" test -x "$HOME/diffsinger/.venv/bin/python"
check "Seed-VC dir" test -f "$HOME/seed-vc/inference.py"
check "Seed-VC venv" test -x "$HOME/seed-vc/.venv/bin/python"
check "fluidsynth" which fluidsynth
check "ffmpeg" which ffmpeg

# LM Studio（作詞／和弦）— 沒開也能用備援，但標成警告
if curl -sf -o /dev/null http://127.0.0.1:1234/v1/models; then
  echo "OK  LM Studio :1234"
  ok=$((ok + 1))
else
  echo "WARN LM Studio :1234 未啟動（步驟 4 AI 作詞會變慢或走模板備援）"
fi

echo "=== $ok ok, $fail failed ==="
# 若 health API 有回，把 JSON 印出來
curl -sf http://127.0.0.1:8080/health 2>/dev/null | python3 -m json.tool 2>/dev/null || true
exit $fail
