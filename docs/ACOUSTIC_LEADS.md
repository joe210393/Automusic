# 原聲音色（主奏＋背景）

## 為什麼 Zeabur 開 log 會 Not Found？

`/tmp/automusic.log` 在你的 **Mac 本機**，不是網站檔案。

錯誤示範：`https://automusic.zeabur.app/web/tmp/automusic.log` → `{"detail":"Not Found"}`

正確看法（在 Mac 終端機）：

```bash
tail -f /tmp/automusic.log
# 或
grep 'render-audio' /tmp/automusic.log | tail -20
```

成功時會看到類似：

```text
[render-audio] 音色：mode=layered program=40 base=MuseScore_General.sf3 overlays=['Sonatina_Orchestra.sf2:ch[0]', 'FingerBass.sf2:ch[1]', ...]
```

## 音色分層

| 層 | 內容 | 音色 |
|----|------|------|
| 主奏 | channel 0 | FreePats／Sonatina 原聲 |
| 背景 | 和聲／裝飾／弦樂墊／貝斯 | 同上（弦樂墊→Sonatina，貝斯→FingerBass） |
| 底 | 鼓與未對應音色 | MuseScore_General |

## 安裝

```bash
cd ~/Automusic
./scripts/install-acoustic-leads.sh
launchctl kickstart -k "gui/$(id -u)/com.automusic.server"
```

## 雲端（Zeabur）怎麼用原聲？

Zeabur 容器只有 FluidR3，沒有 MuseScore／FreePats／Sonatina。  
步驟 3 高音質會自動：

1. 在雲端生成 MIDI  
2. 經 ngrok 呼叫本機 `POST /render-midi`  
3. Mac 用原聲分軌渲染後把 WAV 傳回雲端  

因此要用雲端網站聽到原聲，請保持：

- `com.automusic.server`（:8080）
- `com.automusic.ngrok`
- `soundfonts/` 已裝好（見上方安裝）

檢查本機：

```bash
curl -s http://127.0.0.1:8080/health | python3 -m json.tool
# acoustic_render 應為 true
```

回應標頭 `X-Render-Engine: acoustic-remote` 代表雲端已成功委託 Mac。
