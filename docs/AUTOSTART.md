# 開機就能用（macOS LaunchAgents）

登入 Mac 後會自動啟動：

| 服務 | LaunchAgent | 作用 |
|------|-------------|------|
| Automusic FastAPI | `com.automusic.server` | `:8080`（含 DiffSinger / Seed-VC / 原聲 `/render-midi`） |
| ACE-Step API | `com.automusic.acestep` | `:8001`（AI 整曲人聲，MLX；Zeabur 經 ngrok `/acestep/generate` 委託） |
| ngrok | `com.automusic.ngrok` | 公開網域 → 本機 8080（給 Zeabur 打） |

掛掉會自動重開（`KeepAlive`）。

## 一次安裝

```bash
cd ~/Automusic
chmod +x scripts/install-autostart.sh scripts/automusic-healthcheck.sh scripts/start-acestep-api.sh
./scripts/install-autostart.sh
```

plist 來源在 `launchd/`，安裝時會寫入 `~/Library/LaunchAgents/`。

## 健康檢查

```bash
./scripts/automusic-healthcheck.sh
# 或
curl -s http://127.0.0.1:8080/health | python3 -m json.tool
curl -s http://127.0.0.1:8080/acestep/health | python3 -m json.tool
```

### ACE-Step：5Hz LM 必須載入（Sprint 1）

`thinking=true` 只有在 **5Hz LM 真的載入** 時才有「作曲企劃 → DiT」效果。開機後請確認：

| 欄位 | 期望 |
|------|------|
| `llm_initialized` | `true` |
| `loaded_lm_model` | 例如 `acestep-5Hz-lm-4B`（**不要**是空／No LM） |
| `thinking_effective` | `true`（Automusic 綜合判斷） |

LaunchAgent 腳本預設（Sprint 4）：

- DiT：`acestep-v15-xl-turbo`（可退回 `acestep-v15-turbo`）
- LM：`acestep-5Hz-lm-4B`（`ACESTEP_LM_MODEL_PATH`／`ACESTEP_INIT_LLM=true`；可退回 `1.7B`）
- **不要**設 `ACESTEP_NO_INIT`（需開機載入模型，health 才反映真實狀態）

若 health 顯示 LM 未載，或改過 plist 環境變數沒生效：

1. 看 `/tmp/acestep.err.log`
2. **改 plist 後必須 bootout + bootstrap**（僅 `kickstart` 不會重讀 env）：
   ```bash
   cp ~/Automusic/launchd/com.automusic.acestep.plist ~/Library/LaunchAgents/
   launchctl bootout "gui/$(id -u)/com.automusic.acestep" 2>/dev/null || true
   launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.automusic.acestep.plist
   ```
3. 確認 `scripts/start-acestep-api.sh` 的 `ACESTEP_LM_MODEL_PATH` 不是空

Automusic 預設推論參數（可 env 覆寫）：

- `ACESTEP_MODEL=acestep-v15-xl-turbo`（Sprint 4；可退回 `acestep-v15-turbo`）
- `ACESTEP_LM_MODEL`／LaunchAgent `ACESTEP_LM_MODEL_PATH=acestep-5Hz-lm-4B`（作曲能力較強；可退回 `1.7B`）
- `ACESTEP_SHIFT=3.0`（Turbo／XL-turbo 官方建議；設 `off` 則不傳）
- `ACESTEP_INFERENCE_STEPS=8`（xl-sft 建議改 50）
- `ACESTEP_DURATION_SEC=45`
- `ACESTEP_BATCH_SIZE=2`（一次兩版供選歌；VRAM 不足可改 `1`）
- `ACESTEP_AUDIO_FORMAT=wav`（先 lossless → 母帶 → MP3）
- `ACESTEP_PRODUCTION_CAPTION=1`（完整編曲 caption；`0`＝舊短 prompt，A/B 用）
- `ACESTEP_THINKING=1`

### Sprint 4 模型下載（首次）

```bash
cd ~/ACE-Step-1.5
uv run acestep-download --model acestep-v15-xl-turbo
uv run acestep-download --model acestep-5Hz-lm-4B
# 然後
cp ~/Automusic/launchd/com.automusic.acestep.plist ~/Library/LaunchAgents/
launchctl kickstart -k "gui/$(id -u)/com.automusic.acestep"
curl -s http://127.0.0.1:8080/acestep/health | python3 -m json.tool
```

期望：`loaded_model` 含 `xl-turbo`，`loaded_lm_model` 含 `4B`，`thinking_effective=true`。


## 你還要手動開的一件事

**LM Studio**（步驟 4 AI 作詞）不會由 LaunchAgent 啟動。請在 LM Studio 設定裡打開「登入時啟動／Load on startup」，並讓 server 聽 `1234`。沒開也能用模板備援歌詞。

這與 ACE-Step 的 **5Hz LM** 是兩回事：LM Studio＝作詞；ACE 5Hz LM＝音樂企劃（thinking）。

**第一次**啟動 ACE-Step 會下載模型（約數 GB～10GB+），之後登入即可用。日誌見 `/tmp/acestep.err.log`。

## 日誌

- Automusic：`/tmp/automusic.log`、`/tmp/automusic.err.log`
- ACE-Step：`/tmp/acestep.log`、`/tmp/acestep.err.log`
- ngrok：`/tmp/ngrok.log`、`/tmp/ngrok.out`

## 手動重啟

```bash
launchctl kickstart -k "gui/$(id -u)/com.automusic.server"
launchctl kickstart -k "gui/$(id -u)/com.automusic.acestep"
launchctl kickstart -k "gui/$(id -u)/com.automusic.ngrok"
```
