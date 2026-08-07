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

echo "=== 1) MuseScore_General（底音色）==="
download \
  "https://ftp.osuosl.org/pub/musescore/soundfont/MuseScore_General/MuseScore_General.sf3" \
  "$SFDIR/MuseScore_General.sf3"

echo "=== 2) FreePats 主奏 ==="
if [ ! -f "$LEAD/YDP-GrandPiano.sf2" ]; then
  download "https://freepats.zenvoid.org/Piano/YDP-GrandPiano/YDP-GrandPiano-SF2-20160804.tar.bz2" "$TMP/YDP.tar.bz2"
  rm -rf "$TMP/ydp" && mkdir -p "$TMP/ydp" && tar -xjf "$TMP/YDP.tar.bz2" -C "$TMP/ydp"
  cp "$(find "$TMP/ydp" -name '*.sf2' | head -1)" "$LEAD/YDP-GrandPiano.sf2"
fi
echo "OK YDP-GrandPiano"

if [ ! -f "$LEAD/NylonGuitar.sf2" ]; then
  download "https://freepats.zenvoid.org/Guitar/SpanishClassicalGuitar/SpanishClassicalGuitar-SF2-20190618.7z" "$TMP/Nylon.7z"
  SEVEN=$(command -v 7z || command -v 7zz || true)
  [ -n "$SEVEN" ] || { brew install p7zip; SEVEN=$(command -v 7z || command -v 7zz); }
  rm -rf "$TMP/nylon" && mkdir -p "$TMP/nylon"
  "$SEVEN" x -y -o"$TMP/nylon" "$TMP/Nylon.7z" >/dev/null
  cp "$(find "$TMP/nylon" -name '*.sf2' | head -1)" "$LEAD/NylonGuitar.sf2"
fi
echo "OK NylonGuitar"

if [ ! -f "$LEAD/SteelGuitar.sf2" ]; then
  download "https://freepats.zenvoid.org/Guitar/FSS-SteelStringGuitar/FSS-SteelStringGuitar-small-SF2-20200521.tar.xz" "$TMP/Steel.tar.xz"
  rm -rf "$TMP/steel" && mkdir -p "$TMP/steel" && tar -xJf "$TMP/Steel.tar.xz" -C "$TMP/steel"
  cp "$(find "$TMP/steel" -name '*.sf2' | head -1)" "$LEAD/SteelGuitar.sf2"
fi
echo "OK SteelGuitar"

if [ ! -f "$LEAD/Clarinet.sf2" ]; then
  download "https://freepats.zenvoid.org/Reed/Clarinet1/Clarinet-SF2-20190818.tar.xz" "$TMP/Clarinet.tar.xz"
  rm -rf "$TMP/clar" && mkdir -p "$TMP/clar" && tar -xJf "$TMP/Clarinet.tar.xz" -C "$TMP/clar"
  cp "$(find "$TMP/clar" -name '*.sf2' | head -1)" "$LEAD/Clarinet.sf2"
fi
echo "OK Clarinet"

if [ ! -f "$LEAD/TenorSax.sf2" ]; then
  download "https://freepats.zenvoid.org/Reed/TenorSaxophone/TenorSaxophone-small-SF2-20200717.tar.bz2" "$TMP/Sax.tar.bz2"
  rm -rf "$TMP/sax" && mkdir -p "$TMP/sax" && tar -xjf "$TMP/Sax.tar.bz2" -C "$TMP/sax"
  cp "$(find "$TMP/sax" -name '*.sf2' | head -1)" "$LEAD/TenorSax.sf2"
fi
echo "OK TenorSax"

if [ ! -f "$LEAD/Recorder.sf2" ]; then
  download "https://freepats.zenvoid.org/Wind/Recorder1/Recorder-SF2-20201205.7z" "$TMP/Rec.7z"
  SEVEN=$(command -v 7z || command -v 7zz)
  rm -rf "$TMP/rec" && mkdir -p "$TMP/rec"
  "$SEVEN" x -y -o"$TMP/rec" "$TMP/Rec.7z" >/dev/null
  cp "$(find "$TMP/rec" -name '*.sf2' | head -1)" "$LEAD/Recorder.sf2"
fi
echo "OK Recorder"

if [ ! -f "$LEAD/ConcertHarp.sf2" ]; then
  download "https://freepats.zenvoid.org/OrchestralStrings/ConcertHarp/ConcertHarp-small-SF2-20200702.tar.xz" "$TMP/Harp.tar.xz"
  rm -rf "$TMP/harp" && mkdir -p "$TMP/harp" && tar -xJf "$TMP/Harp.tar.xz" -C "$TMP/harp"
  cp "$(find "$TMP/harp" -name '*.sf2' | head -1)" "$LEAD/ConcertHarp.sf2"
fi
echo "OK ConcertHarp"

echo "=== 3) FingerBass（背景貝斯）==="
if [ ! -f "$LEAD/FingerBass.sf2" ]; then
  download "https://github.com/freepats/electric-bass-YR/releases/download/2019-09-30/FingerBassYR-SF2-20190930.7z" "$TMP/Bass.7z"
  SEVEN=$(command -v 7z || command -v 7zz)
  rm -rf "$TMP/bass" && mkdir -p "$TMP/bass"
  "$SEVEN" x -y -o"$TMP/bass" "$TMP/Bass.7z" >/dev/null
  cp "$(find "$TMP/bass" -name '*.sf2' | head -1)" "$LEAD/FingerBass.sf2"
fi
echo "OK FingerBass"

echo "=== 4) Sonatina 管弦（小提琴／長笛／小號／弦樂鋪底，約 470MB）==="
if [ ! -f "$LEAD/Sonatina_Orchestra.sf2" ]; then
  download "https://ftp.osuosl.org/pub/musescore/soundfont/Sonatina_Symphonic_Orchestra_SF2.zip" "$TMP/sonatina.zip"
  rm -rf "$TMP/sonatina" && mkdir -p "$TMP/sonatina"
  unzip -q "$TMP/sonatina.zip" -d "$TMP/sonatina"
  cp "$(find "$TMP/sonatina" -name 'Sonatina_Symphonic_Orchestra.sf2' | head -1)" "$LEAD/Sonatina_Orchestra.sf2"
fi
echo "OK Sonatina_Orchestra"

echo ""
echo "=== 安裝完成 ==="
ls -lh "$SFDIR/MuseScore_General.sf3" "$LEAD"/*.sf2
echo ""
echo "看本機 log（不是網站）：tail -f /tmp/automusic.log"
echo "重啟：launchctl kickstart -k \"gui/\$(id -u)/com.automusic.server\""
