# 主奏原聲音色（Acoustic Leads）

步驟 3 以前用 `GeneralUserGS`，電子感較重。現在改成：

| 層級 | 音色 | 用途 |
|------|------|------|
| 底 | **MuseScore_General.sf3** | 整曲 GM（弦樂／長笛／銅管等也比較不電） |
| 主奏覆寫 | **FreePats** 真實取樣 | 鋼琴、尼龍吉他、鋼弦吉他、豎笛 |

主奏是「真樂器取樣」；小提琴／長笛等尚未單獨覆寫時，走 MuseScore 底庫（仍比舊 GeneralUser 自然）。

## 安裝（本機一次）

```bash
cd ~/Automusic
chmod +x scripts/install-acoustic-leads.sh
./scripts/install-acoustic-leads.sh
launchctl kickstart -k "gui/$(id -u)/com.automusic.server"
```

檔案會放在 `soundfonts/`（已 gitignore，約 180MB）。

## 怎麼判斷有沒有效

步驟 3 選偏鋼琴／民謠／豎笛的風格，按「製作歌曲」後聽主旋律：
- log 應出現：`[render-audio] 音色：mode=lead_overlay … lead=YDP-GrandPiano.sf2`（或 Nylon／Steel／Clarinet）
- 若 `mode=base_only`，代表這次主奏樂器還沒有專用取樣（例如小提琴），只用 MuseScore 底庫

## 授權摘要

- MuseScore_General：見官方 License（MIT／樣本來源標註）
- FreePats 鋼琴／吉他／豎笛：CC0 或 CC-BY（詳見 FreePats 各頁）

## 下一步（可選）

若主奏滿意，再整包升級伴奏／弦樂／銅管（例如更大的管弦取樣庫）。
