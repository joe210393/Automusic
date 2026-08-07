# 主奏原聲音色（Acoustic Leads）

步驟 3 用兩層音色：

| 層級 | 音色 | 用途 |
|------|------|------|
| 底 | **MuseScore_General.sf3** | 伴奏 GM |
| 主奏覆寫 | FreePats + **Sonatina** | 只換主旋律樂器 |

## 已涵蓋的主奏

- 鋼琴、尼龍／鋼弦吉他、豎琴  
- 豎笛、直笛、薩克斯風  
- 小提琴／大提琴、長笛／短笛、雙簧管、小號／法國號／長號（Sonatina）

鼓、貝斯、和聲墊底仍走底庫（不是這波範圍）。

## 安裝

```bash
cd ~/Automusic
./scripts/install-acoustic-leads.sh
launchctl kickstart -k "gui/$(id -u)/com.automusic.server"
```

約需 **650MB+** 磁碟（含 Sonatina）。`soundfonts/` 已 gitignore。

## 怎麼確認成功

步驟 3 製作後，log（`/tmp/automusic.log`）應出現：

```text
[render-audio] 音色：mode=lead_overlay program=40 lead=Sonatina_Orchestra.sf2 …
```

`mode=lead_overlay` = 主奏原聲有套上；`base_only` = 該次主奏還沒對應取樣（少見）。
