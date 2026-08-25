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
check "LaunchAgent acestep loaded" launchctl print "gui/$(id -u)/com.automusic.acestep"
check "FastAPI :8080" curl -sf -o /dev/null -w '' http://127.0.0.1:8080/docs
check "Health API" curl -sf http://127.0.0.1:8080/health
check "ACE-Step API :8001" curl -sf http://127.0.0.1:8001/health

# ACE 5Hz LM（thinking 依賴；與 LM Studio 作詞不同）
if ACE_JSON="$(curl -sf http://127.0.0.1:8001/health 2>/dev/null)"; then
  ACE_LM_LINE="$(printf '%s' "$ACE_JSON" | python3 -c "
import json,sys
wrap=json.load(sys.stdin)
data=wrap.get('data') if isinstance(wrap,dict) else wrap
data=data if isinstance(data,dict) else {}
lm=str(data.get('loaded_lm_model') or '').strip()
model=str(data.get('loaded_model') or '').strip()
llm=bool(data.get('llm_initialized'))
print(f'model={model or \"?\"} lm={lm or \"(none)\"}')
sys.exit(0 if (llm and lm and lm.lower() not in ('none','null','no lm')) else 1)
" 2>/dev/null)" && {
    echo "OK  ACE-Step 5Hz LM loaded ($ACE_LM_LINE)"
    ok=$((ok + 1))
  } || {
    echo "NG  ACE-Step 5Hz LM NOT loaded (${ACE_LM_LINE:-check /tmp/acestep.err.log})"
    fail=$((fail + 1))
  }
fi
if curl -sf http://127.0.0.1:8080/acestep/health 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); raise SystemExit(0 if d.get('thinking_effective') else 1)" 2>/dev/null; then
  echo "OK  ACE thinking_effective"
  ok=$((ok + 1))
else
  echo "NG  ACE thinking_effective"
  fail=$((fail + 1))
fi

check "ngrok local API" curl -sf http://127.0.0.1:4040/api/tunnels
check "ACE-Step dir" test -d "$HOME/ACE-Step-1.5/.venv"
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
