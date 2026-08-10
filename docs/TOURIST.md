# 遊客體驗站（蘇澳）

## 路徑

| 路徑 | 用途 |
|------|------|
| `/` | 遊客體驗：主首頁選站 → 城鎮站 → 旅程 7 步驟 |
| `/web/` | 工程實驗室（原 Demo，保留） |
| `/admin` | 內容後台（新增／編輯旅程路線） |
| `/login` | 遊客登入 |
| `/register` | 遊客註冊 |
| `/me` | 使用者後台（帳號與我的旅程） |
| `/s/{slug}` | 公開分享頁 |
| `/api/journey/*` | 旅程適配 API |
| `/api/destinations` | 目的地內容包（遊客讀取） |
| `/api/admin/*` | 後台寫入 API（需 `ADMIN_TOKEN`） |
| `/api/account/*` | 簡易帳號／額度 |

## 架構

- 音樂引擎（melody／midi／voice／soundfont）不改合約
- 每趟旅程獨立目錄：`journeys/{id}/`（Zeabur 若有 `/voice` → `/voice/journeys`）
- 內容包 seed：`app/content/destinations/*.json`
- 後台寫入目錄：`content/destinations/`（本機）或 `/voice/content/destinations/`（Zeabur）；啟動時會從 seed 複製一份，之後以可寫目錄為準
- 遊客「選擇旅程」完全吃內容包的 `routes[]`，不要再改前端硬編碼
- 歌詞關鍵字由遊客自行輸入（`meta.keywords`），不使用系統預設 chips／地名注入
- 後台為分頁編輯：目的地列表 → 選項目（品牌／旅程／心情）→ 單頁儲存
- 後台「遊客紀錄」`#/activity`：看遊客玩了／錄了／產出了什麼（`GET /api/admin/activity`）
- 帳號：`POST /api/account/register` 註冊、`POST /api/account/login` 登入（email 必須已註冊）
- 登入後首頁只顯示暱稱／email，旅程作品改在 `/me` 使用者後台查看
- `/me` 可命名旅程、上傳封面、查看錄音／歌詞／成品；未完成可繼續、已完成可回看（`/?journey=id`）
- 後台品牌文案可用「LM 自動生成」再手動存檔
- 後台與登入／註冊頁皆使用全幅風景背景

## 後台設定

```bash
# 環境變數（本機 LaunchAgent / Zeabur 都要設）
export ADMIN_TOKEN='你的長隨機字串'

# 開啟後台
open http://127.0.0.1:8080/admin
# 登入後：目的地 → 旅程路線 → 新增／編輯單條旅程 → 儲存這一頁
```

## 本機驗證

```bash
launchctl kickstart -k "gui/$(id -u)/com.automusic.server"
curl -s http://127.0.0.1:8080/api/destinations | python3 -m json.tool
open http://127.0.0.1:8080/
open http://127.0.0.1:8080/web/
```
