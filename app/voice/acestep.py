"""
本機 ACE-Step 1.5 API 客戶端：文字＋歌詞 → 整曲（含人聲）。

預設打 http://127.0.0.1:8001（LaunchAgent com.automusic.acestep）。
失敗時由呼叫端 fallback 到編曲／主旋律路徑。
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests

ACESTEP_URL = os.getenv("ACESTEP_URL", "http://127.0.0.1:8001").rstrip("/")
ACESTEP_API_KEY = (os.getenv("ACESTEP_API_KEY") or "").strip()
ACESTEP_MODEL = os.getenv("ACESTEP_MODEL", "acestep-v15-turbo")
# thinking=true 需要 5Hz LM；本機為加速預設關閉（純 DiT 仍可唱歌詞）
ACESTEP_THINKING = os.getenv("ACESTEP_THINKING", "0").strip().lower() in ("1", "true", "yes")
ACESTEP_POLL_INTERVAL = float(os.getenv("ACESTEP_POLL_INTERVAL", "2.0"))
ACESTEP_TIMEOUT_SEC = float(os.getenv("ACESTEP_TIMEOUT_SEC", "900"))

ProgressCb = Optional[Callable[[int, str], None]]

# 歌手模板 → ACE-Step prompt 風格提示
SINGER_PROMPTS: Dict[str, str] = {
    "female_bright": "bright clear female vocals, sunny Mandarin pop, travel souvenir song",
    "female_warm": "warm soft female vocals, gentle Mandarin pop ballad, nostalgic travel song",
    "female_soft": "soft intimate female vocals, quiet Mandarin acoustic pop, seaside memory",
    "male_deep": "deep rich male vocals, warm Mandarin pop, grounded travel anthem",
    "male_warm": "warm mid male vocals, heartfelt Mandarin pop, coastal journey song",
    "male_clear": "clear strong male vocals, uplifting Mandarin pop, memorable travel song",
}


def is_available(timeout: float = 1.5) -> bool:
    try:
        r = requests.get(f"{ACESTEP_URL}/health", timeout=timeout)
        if r.status_code != 200:
            return False
        data = r.json()
        # ACE-Step wraps as {data, code} or may return plain
        if isinstance(data, dict) and "code" in data:
            return int(data.get("code") or 0) == 200
        return True
    except Exception:
        return False


def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if ACESTEP_API_KEY:
        h["Authorization"] = f"Bearer {ACESTEP_API_KEY}"
    return h


def format_lyrics(lyrics: dict) -> str:
    title = (lyrics.get("title") or "").strip()
    verse = (lyrics.get("verse") or "").strip()
    chorus = (lyrics.get("chorus") or "").strip()
    parts = []
    if title:
        parts.append(f"[title]\n{title}")
    if verse:
        parts.append(f"[verse]\n{verse}")
    if chorus:
        parts.append(f"[chorus]\n{chorus}")
    return "\n\n".join(parts).strip()


def format_key_scale(key: Optional[str]) -> str:
    raw = (key or "").strip()
    if not raw:
        return ""
    # Already "C Major" / "A Minor"
    if " " in raw:
        return raw
    # Am / A#m / F#m
    if raw.endswith("m") and not raw.lower().endswith("major"):
        root = raw[:-1]
        return f"{root} Minor"
    return f"{raw} Major"


def build_prompt(
    *,
    singer_id: Optional[str],
    engine_style: Optional[str],
    title: Optional[str] = None,
) -> str:
    base = SINGER_PROMPTS.get(singer_id or "", "Mandarin pop song with natural vocals, travel memory")
    bits = [base, "Chinese vocals", "complete song with accompaniment and singing"]
    if engine_style:
        bits.append(str(engine_style))
    if title:
        bits.append(f"song title vibe: {title}")
    return ", ".join(bits)


def _parse_result_payload(raw: Any) -> Optional[dict]:
    if raw is None:
        return None
    if isinstance(raw, list) and raw:
        item = raw[0]
        return item if isinstance(item, dict) else None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return _parse_result_payload(parsed)
        except Exception:
            return None
    return None


def _audio_url_from_result(item: dict) -> Optional[str]:
    file_ref = item.get("file") or item.get("audio") or item.get("path")
    if not file_ref:
        return None
    file_ref = str(file_ref)
    if file_ref.startswith("http://") or file_ref.startswith("https://"):
        return file_ref
    if file_ref.startswith("/"):
        return f"{ACESTEP_URL}{file_ref}"
    # bare path → /v1/audio?path=
    return f"{ACESTEP_URL}/v1/audio?path={urllib.parse.quote(file_ref, safe='')}"


def generate_to_file(
    *,
    lyrics: dict,
    bpm: float,
    key: Optional[str],
    singer_id: Optional[str] = None,
    engine_style: Optional[str] = None,
    duration_sec: float = 45.0,
    out_path: Path,
    progress: ProgressCb = None,
) -> Path:
    """
    呼叫 ACE-Step 產生整曲並寫入 out_path（建議 .mp3 / .wav）。
    成功回傳 out_path；失敗 raise RuntimeError。
    """
    if progress:
        progress(20, "連線 AI 唱歌引擎")

    lyric_text = format_lyrics(lyrics)
    if not lyric_text:
        raise RuntimeError("歌詞為空，無法產生 AI 人聲版")

    prompt = build_prompt(
        singer_id=singer_id,
        engine_style=engine_style,
        title=lyrics.get("title"),
    )
    duration_sec = max(10.0, min(120.0, float(duration_sec)))
    bpm_i = int(max(30, min(300, round(float(bpm))))) if bpm else None

    body: Dict[str, Any] = {
        "prompt": prompt,
        "lyrics": lyric_text,
        "vocal_language": "zh",
        "thinking": ACESTEP_THINKING,
        "audio_format": "mp3" if str(out_path).lower().endswith(".mp3") else "wav",
        "model": ACESTEP_MODEL,
        "audio_duration": duration_sec,
        "time_signature": "4",
    }
    if bpm_i:
        body["bpm"] = bpm_i
    key_scale = format_key_scale(key)
    if key_scale:
        body["key_scale"] = key_scale
    if ACESTEP_API_KEY:
        body["ai_token"] = ACESTEP_API_KEY

    r = requests.post(
        f"{ACESTEP_URL}/release_task",
        headers=_headers(),
        json=body,
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"ACE-Step release_task HTTP {r.status_code}: {r.text[:300]}")
    wrap = r.json()
    if isinstance(wrap, dict) and wrap.get("code") not in (None, 200):
        raise RuntimeError(f"ACE-Step release_task error: {wrap.get('error') or wrap}")
    data = wrap.get("data") if isinstance(wrap, dict) else wrap
    task_id = None
    if isinstance(data, dict):
        task_id = data.get("task_id") or data.get("id")
    elif isinstance(data, str):
        task_id = data
    if not task_id:
        raise RuntimeError(f"ACE-Step 未回傳 task_id: {wrap}")

    if progress:
        progress(35, "AI 正在作曲唱歌")

    deadline = time.time() + ACESTEP_TIMEOUT_SEC
    last_status = None
    while time.time() < deadline:
        qr = requests.post(
            f"{ACESTEP_URL}/query_result",
            headers=_headers(),
            json={"task_id_list": [task_id]},
            timeout=30,
        )
        if qr.status_code >= 400:
            raise RuntimeError(f"ACE-Step query_result HTTP {qr.status_code}: {qr.text[:300]}")
        qwrap = qr.json()
        qdata = qwrap.get("data") if isinstance(qwrap, dict) else qwrap
        row = None
        if isinstance(qdata, list) and qdata:
            row = qdata[0]
        elif isinstance(qdata, dict):
            row = qdata
        if not isinstance(row, dict):
            time.sleep(ACESTEP_POLL_INTERVAL)
            continue

        status = row.get("status")
        last_status = status
        if status == 1:
            item = _parse_result_payload(row.get("result"))
            if not item:
                raise RuntimeError("ACE-Step 成功但結果為空")
            audio_url = _audio_url_from_result(item)
            if not audio_url:
                raise RuntimeError(f"ACE-Step 結果缺少音檔: {item}")
            if progress:
                progress(85, "下載成品")
            ar = requests.get(audio_url, headers=_headers(), timeout=120)
            if ar.status_code >= 400:
                raise RuntimeError(f"ACE-Step 下載音檔失敗 HTTP {ar.status_code}")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(ar.content)
            if progress:
                progress(95, "輸出成品")
            return out_path
        if status == 2:
            err = row.get("error") or row.get("message") or row.get("result") or "unknown"
            raise RuntimeError(f"ACE-Step 產生失敗: {err}")

        # still running
        if progress:
            progress(55, "AI 正在作曲唱歌")
        time.sleep(ACESTEP_POLL_INTERVAL)

    raise RuntimeError(f"ACE-Step 逾時（status={last_status}）")
