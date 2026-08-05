# Music Education MVP

一個「音樂教育 × AI 創作」的最小可行服務，用於課程與展示。

## 專案目的

本專案旨在幫助學生將自製樂器演奏的單音旋律，透過 AI 自動編曲和歌詞生成，完成一首可播放、可保存的音樂作品。

## 使用流程

1. 學生使用自製樂器演奏 5–15 秒「單音旋律」
2. 系統辨識學生演奏的音符與節奏（允許誤差）
3. 系統保留學生旋律，自動補上簡單、穩定、不搶戲的伴奏
4. 學生與家長輸入幾個關鍵字或一句話
5. 系統將文字整理成簡單、溫暖、適合親子課程的歌詞
6. 最終輸出一首可播放、可保存的 MIDI 音樂作品

## 技術架構

- **Backend**: Python + FastAPI
- **音訊處理**: aubio（pitch detection + onset detection）
- **MIDI 生成**: mido
- **編曲規則**: 規則法（不使用機器學習）
- **歌詞生成**: 模板規則（不使用外部 API）

## 專案結構

```
music-mvp/
├─ app/
│  ├─ main.py              # FastAPI 主程式
│  ├─ audio/
│  │  ├─ extract_notes.py  # wav -> notes
│  │  ├─ quantize.py       # notes -> quantized notes
│  │  └─ key_detect.py     # notes -> key
│  ├─ arrange/
│  │  ├─ chords.py         # key + notes -> bar chords
│  │  └─ patterns.py       # pop/education pattern 定義
│  ├─ midi/
│  │  └─ generate_midi.py  # notes + chords + patterns -> full.mid
│  ├─ lyrics/
│  │  └─ generator.py      # keywords + emotion -> lyrics
│  └─ utils/
│     └─ files.py          # 暫存檔處理
├─ requirements.txt
├─ Dockerfile
└─ README.md
```

## API 規格

### 1. POST /generate-lyrics

根據關鍵字和情緒生成歌詞。

**請求**:
```json
{
  "keywords": ["開心", "第一次", "自己做的樂器"],
  "emotion": "溫暖"
}
```

**回應**:
```json
{
  "verse": "輕輕地，我們一起，開心 第一次 自己做的樂器，唱出心中的歌。",
  "chorus": "這是，創造的，開心 第一次 自己做的樂器，屬於我們的旋律。"
}
```

### 2. POST /analyze-audio

上傳 WAV 檔案，辨識音符、BPM 和調性。

**請求**: multipart/form-data，欄位名 `file`

**回應**:
```json
{
  "notes": [
    {"start": 0.0, "end": 0.5, "midi": 60, "velocity": 90}
  ],
  "bpm": 90.0,
  "key": "C"
}
```

### 3. POST /render-music

根據音符、BPM、調性和歌詞生成完整的 MIDI 檔案。

**請求**:
```json
{
  "notes": [{"start": 0.0, "end": 0.5, "midi": 60, "velocity": 90}],
  "bpm": 90.0,
  "key": "C",
  "lyrics": {
    "verse": "...",
    "chorus": "..."
  }
}
```

**回應**: 下載 `full.mid` 檔案（application/octet-stream）

## 步驟 6：系統代唱（DiffSinger）＋ 聲紋轉換（Seed-VC）

步驟 6 的流程是：

1. **DiffSinger** 依旋律＋歌詞唱出乾聲（使用者不必會唱）
2. **Seed-VC** 用步驟 5 的聲紋換成你的音色
3. 混進步驟 3 的伴奏

本機需另外安裝 DiffSinger 與 Seed-VC（皆佔用數 GB）。詳見 [docs/DIFFSINGER.md](docs/DIFFSINGER.md)。
安裝完成後，把 [`scripts/diffsinger_infer_cli.py`](scripts/diffsinger_infer_cli.py) 複製為 `~/diffsinger/infer_cli.py`。

雲端（Zeabur）會經 ngrok 呼叫本機的 `/svs/synthesize` 與 `/vc/convert`。

## 開機就能用（推薦）

登入 Mac 後自動啟動 FastAPI + ngrok（掛掉會重開）：

```bash
./scripts/install-autostart.sh
./scripts/automusic-healthcheck.sh
```

詳見 [docs/AUTOSTART.md](docs/AUTOSTART.md)。LM Studio 請在 App 內開啟「登入時啟動」。

## 本機啟動方式

### 1. 安裝依賴

```bash
# 安裝系統依賴（macOS）
brew install aubio libsndfile

# 安裝 Python 依賴
pip install -r requirements.txt
```

### 2. 啟動服務

```bash
# 一次性手動啟動（開發用 --reload）
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

# 或用 LaunchAgent（開機自動、KeepAlive）
./scripts/install-autostart.sh
```

服務將在 `http://localhost:8080` 啟動。

### 3. 查看 API 文件

訪問 `http://localhost:8080/docs` 查看 Swagger UI；`http://localhost:8080/health` 看 DiffSinger／Seed-VC 是否就緒。

## 測試指令

### 測試歌詞生成

```bash
curl -X POST "http://localhost:8080/generate-lyrics" \
  -H "Content-Type: application/json" \
  -d '{
    "keywords": ["開心", "第一次", "自己做的樂器"],
    "emotion": "溫暖"
  }'
```

### 測試音訊分析

```bash
curl -X POST "http://localhost:8080/analyze-audio" \
  -F "file=@test_audio.wav"
```

### 測試 MIDI 生成

```bash
# 先取得 analyze-audio 的結果，然後：
curl -X POST "http://localhost:8080/render-music" \
  -H "Content-Type: application/json" \
  -d '{
    "notes": [{"start": 0.0, "end": 0.5, "midi": 60, "velocity": 90}],
    "bpm": 90.0,
    "key": "C",
    "lyrics": {
      "verse": "輕輕地，我們一起，唱出心中的歌。",
      "chorus": "這是，創造的，屬於我們的旋律。"
    }
  }' \
  --output full.mid
```

## Zeabur 部署

### 部署步驟

1. 將專案推送到 Git 倉庫（GitHub/GitLab）
2. 在 Zeabur 建立新專案
3. 連接 Git 倉庫
4. Zeabur 會自動偵測 Dockerfile 並建置

### 注意事項

- **Port**: 服務必須監聽 `8080` 端口（已在 Dockerfile 設定）
- **暫存檔**: 所有產生的檔案存放在 `/tmp` 目錄
- **環境變數**: 目前不需要額外環境變數

### Docker 建置測試

```bash
# 建置映像
docker build -t music-mvp .

# 執行容器
docker run -p 8080:8080 music-mvp
```

## 開發里程碑

### ✅ Milestone A: FastAPI 基礎架構 + 歌詞生成

- [x] 建立專案結構
- [x] FastAPI 主程式
- [x] `/generate-lyrics` API 實作
- [x] 模板式歌詞生成器

**測試方式**:
```bash
curl -X POST "http://localhost:8080/generate-lyrics" \
  -H "Content-Type: application/json" \
  -d '{"keywords": ["開心", "第一次"], "emotion": "溫暖"}'
```

**已知限制**:
- 歌詞模板較為簡單，僅支援 3 種情緒（溫暖、開心、平靜）
- 關鍵字整合方式較為直接，未做語義分析

### 🔄 Milestone B: 音訊分析

- [x] `/analyze-audio` API 實作
- [x] 使用 aubio 進行 pitch detection 和 onset detection
- [x] 音符量化（1/8 拍格點）
- [x] 簡化版調性檢測

**測試方式**:
```bash
curl -X POST "http://localhost:8080/analyze-audio" \
  -F "file=@test_audio.wav"
```

**已知限制**:
- 單音旋律辨識較為穩定，多音同時演奏可能產生誤差
- BPM 檢測為簡化版，根據音符密度估算
- 調性檢測僅考慮大調，且優先常見調性

### 🔄 Milestone C: 固定和弦 MIDI 生成

- [x] `/render-music` API 實作
- [x] 固定和弦 loop 的 MIDI 生成（驗證編曲/輸出）

**測試方式**:
```bash
curl -X POST "http://localhost:8080/render-music" \
  -H "Content-Type: application/json" \
  -d '{
    "notes": [{"start": 0.0, "end": 0.5, "midi": 60, "velocity": 90}],
    "bpm": 90.0,
    "key": "C",
    "lyrics": {"verse": "測試", "chorus": "測試"}
  }' \
  --output full.mid
```

**已知限制**:
- 目前使用固定和弦進行測試，尚未接上規則推斷

### 🔄 Milestone D: 完整編曲規則

- [x] 和弦推斷規則（根據旋律音符）
- [x] Pattern 編曲（drums, bass, harmony）
- [x] 完整流程整合

**已知限制**:
- 編曲規則較為簡單，僅支援 4/4 拍
- 和弦選擇優先順序固定，未考慮音樂理論的進階規則
- Pattern 僅實作整拍和弦，分解和弦可後續擴展

## 設計原則

- **穩定性優先**: 系統設計以穩定輸出為目標，允許一定誤差
- **規則清楚**: 所有邏輯使用規則法，不使用黑盒模型，適合教學說明
- **結果可變**: 每個學生的輸入都會產生不同的輸出
- **教育導向**: 系統風格偏向教育/展示，避免過度複雜

## 授權

本專案用於教育與展示目的。
