# 開機就能用（macOS LaunchAgents）

登入 Mac 後會自動啟動：

| 服務 | LaunchAgent | 作用 |
|------|-------------|------|
| Automusic FastAPI | `com.automusic.server` | `:8080`（含 DiffSinger / Seed-VC） |
| ngrok | `com.automusic.ngrok` | 公開網域 → 本機 8080（給 Zeabur 打） |

掛掉會自動重開（`KeepAlive`）。

## 一次安裝

```bash
cd ~/Automusic
chmod +x scripts/install-autostart.sh scripts/automusic-healthcheck.sh
./scripts/install-autostart.sh
```

plist 來源在 `launchd/`，安裝時會寫入 `~/Library/LaunchAgents/`。

## 健康檢查

```bash
./scripts/automusic-healthcheck.sh
# 或
curl -s http://127.0.0.1:8080/health | python3 -m json.tool
```

## 你還要手動開的一件事

**LM Studio**（步驟 4 AI 作詞）不會由 LaunchAgent 啟動。請在 LM Studio 設定裡打開「登入時啟動／Load on startup」，並讓 server 聽 `1234`。沒開也能用模板備援歌詞。

## 日誌

- Automusic：`/tmp/automusic.log`、`/tmp/automusic.err.log`
- ngrok：`/tmp/ngrok.log`、`/tmp/ngrok.out`

## 手動重啟

```bash
launchctl kickstart -k "gui/$(id -u)/com.automusic.server"
launchctl kickstart -k "gui/$(id -u)/com.automusic.ngrok"
```
