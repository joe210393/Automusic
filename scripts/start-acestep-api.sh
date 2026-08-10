#!/bin/zsh
# Non-interactive ACE-Step API launcher for LaunchAgent / health scripts.
set -euo pipefail

ACE_ROOT="${ACESTEP_DIR:-$HOME/ACE-Step-1.5}"
HOST="${ACESTEP_API_HOST:-127.0.0.1}"
PORT="${ACESTEP_API_PORT:-8001}"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
export HOME="${HOME:-/Users/hung-weichen}"
export ACESTEP_LM_BACKEND="${ACESTEP_LM_BACKEND:-mlx}"
export ACESTEP_INIT_LLM="${ACESTEP_INIT_LLM:-true}"
export ACESTEP_NO_INIT="${ACESTEP_NO_INIT:-true}"
export ACESTEP_CONFIG_PATH="${ACESTEP_CONFIG_PATH:-acestep-v15-turbo}"
export ACESTEP_LM_MODEL_PATH="${ACESTEP_LM_MODEL_PATH:-acestep-5Hz-lm-1.7B}"
export ACESTEP_API_HOST="$HOST"
export ACESTEP_API_PORT="$PORT"
export TOKENIZERS_PARALLELISM=false
# Avoid Hugging Face interactive prompts under launchd
export HF_HUB_DISABLE_PROGRESS_BARS="${HF_HUB_DISABLE_PROGRESS_BARS:-1}"
export PYTHONUNBUFFERED=1

if [[ ! -d "$ACE_ROOT/.venv" ]]; then
  echo "[acestep] missing venv at $ACE_ROOT/.venv — run: cd $ACE_ROOT && uv sync" >&2
  exit 1
fi

cd "$ACE_ROOT"

UV_BIN="$(command -v uv || true)"
if [[ -z "$UV_BIN" ]]; then
  echo "[acestep] uv not found in PATH" >&2
  exit 1
fi

echo "[acestep] starting API at http://${HOST}:${PORT} (cwd=$ACE_ROOT)"
exec "$UV_BIN" run --offline acestep-api --host "$HOST" --port "$PORT" --no-init || \
  exec "$UV_BIN" run acestep-api --host "$HOST" --port "$PORT" --no-init
