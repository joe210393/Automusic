#!/bin/zsh
# 安裝「主奏原聲」音色庫到 soundfonts/（不進 git，檔案較大）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SFDIR="$ROOT/soundfonts"
LEAD="$SFDIR/leads"
TMP="${TMPDIR:-/tmp}/automusic_sf_dl"
mkdir -p "$SFDIR" "$LEAD" "$TMP"
cd "$TMP"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

download() {
  local url="$1" out="$2"
  if [ -f "$out" ]; then
    echo "OK 已存在 $(basename "$out")"
    return
  fi
  echo "↓ 下載 $(basename "$out") …"
  curl -L --fail --progress-bar -o "$out.download" "$url"
  mv "$out.download" "$out"
}

echo "=== 1) MuseScore_General（底音色，弦樂／長笛／銅管也會比較不電）==="
download \
  "https://ftp.osuosl.org/pub/musescore/soundfont/MuseScore_General/MuseScore_General.sf3" \
  "$SFDIR/MuseScore_General.sf3"

echo "=== 2) FreePats 主奏原聲（鋼琴／吉他／豎笛）==="
if [ ! -f "$LEAD/YDP-GrandPiano.sf2" ]; then
  download \
    "https://freepats.zenvoid.org/Piano/YDP-GrandPiano/YDP-GrandPiano-SF2-20160804.tar.bz2" \
    "$TMP/YDP.tar.bz2"
  rm -rf "$TMP/ydp" && mkdir -p "$TMP/ydp"
  tar -xjf "$TMP/YDP.tar.bz2" -C "$TMP/ydp"
  cp "$(find "$TMP/ydp" -name '*.sf2' | head -1)" "$LEAD/YDP-GrandPiano.sf2"
fi
echo "OK YDP-GrandPiano.sf2"

if [ ! -f "$LEAD/NylonGuitar.sf2" ]; then
  download \
    "https://freepats.zenvoid.org/Guitar/SpanishClassicalGuitar/SpanishClassicalGuitar-SF2-20190618.7z" \
    "$TMP/Nylon.7z"
  SEVEN=$(command -v 7z || command -v 7zz || true)
  if [ -z "$SEVEN" ]; then
    echo "需要 p7zip：brew install p7zip"
    brew install p7zip
    SEVEN=$(command -v 7z || command -v 7zz)
  fi
  rm -rf "$TMP/nylon" && mkdir -p "$TMP/nylon"
  "$SEVEN" x -y -o"$TMP/nylon" "$TMP/Nylon.7z" >/dev/null
  cp "$(find "$TMP/nylon" -name '*.sf2' | head -1)" "$LEAD/NylonGuitar.sf2"
fi
echo "OK NylonGuitar.sf2"

if [ ! -f "$LEAD/SteelGuitar.sf2" ]; then
  download \
    "https://freepats.zenvoid.org/Guitar/FSS-SteelStringGuitar/FSS-SteelStringGuitar-small-SF2-20200521.tar.xz" \
    "$TMP/Steel.tar.xz"
  rm -rf "$TMP/steel" && mkdir -p "$TMP/steel"
  tar -xJf "$TMP/Steel.tar.xz" -C "$TMP/steel"
  cp "$(find "$TMP/steel" -name '*.sf2' | head -1)" "$LEAD/SteelGuitar.sf2"
fi
echo "OK SteelGuitar.sf2"

if [ ! -f "$LEAD/Clarinet.sf2" ]; then
  download \
    "https://freepats.zenvoid.org/Reed/Clarinet1/Clarinet-SF2-20190818.tar.xz" \
    "$TMP/Clarinet.tar.xz"
  rm -rf "$TMP/clar" && mkdir -p "$TMP/clar"
  tar -xJf "$TMP/Clarinet.tar.xz" -C "$TMP/clar"
  cp "$(find "$TMP/clar" -name '*.sf2' | head -1)" "$LEAD/Clarinet.sf2"
fi
echo "OK Clarinet.sf2"

echo ""
echo "=== 安裝完成 ==="
ls -lh "$SFDIR/MuseScore_General.sf3" "$LEAD"/*.sf2
echo ""
echo "重啟 Automusic 後步驟 3 會自動使用："
echo "  - 底：MuseScore_General"
echo "  - 主奏覆寫：鋼琴／尼龍吉他／鋼弦吉他／豎笛"
echo "  launchctl kickstart -k \"gui/\$(id -u)/com.automusic.server\""
