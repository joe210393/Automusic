"""
本機 ACE-Step 1.5 API 客戶端：文字＋歌詞 → 整曲（含人聲）。

- 本機 Automusic：打 http://127.0.0.1:8001
- 雲端 Zeabur：經 ngrok 打本機 Automusic 的 /acestep/generate（與 DiffSinger／Seed-VC 同模式）
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

ACESTEP_URL = os.getenv("ACESTEP_URL", "http://127.0.0.1:8001").rstrip("/")
ACESTEP_API_KEY = (os.getenv("ACESTEP_API_KEY") or "").strip()
ACESTEP_MODEL = os.getenv("ACESTEP_MODEL", "acestep-v15-turbo")
# thinking=true 用 5Hz LM 規劃，人聲明顯較穩；需本機 ACE-Step 已載入 LM
ACESTEP_THINKING = os.getenv("ACESTEP_THINKING", "1").strip().lower() not in ("0", "false", "no")
# Turbo 官方建議 shift=3.0；設為 off/none 則不傳（A/B 對照用）
_ACESTEP_SHIFT_RAW = (os.getenv("ACESTEP_SHIFT", "3.0") or "").strip().lower()
ACESTEP_SHIFT: Optional[float]
if _ACESTEP_SHIFT_RAW in ("", "off", "none", "default"):
    ACESTEP_SHIFT = None
else:
    try:
        ACESTEP_SHIFT = float(_ACESTEP_SHIFT_RAW)
    except ValueError:
        ACESTEP_SHIFT = 3.0
# 1=完整編曲 caption（預設）；0=舊短 prompt（A/B）
ACESTEP_PRODUCTION_CAPTION = os.getenv("ACESTEP_PRODUCTION_CAPTION", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
ACESTEP_POLL_INTERVAL = float(os.getenv("ACESTEP_POLL_INTERVAL", "2.0"))
ACESTEP_TIMEOUT_SEC = float(os.getenv("ACESTEP_TIMEOUT_SEC", "900"))
# 旅程成品預設長度（秒）；本機 API 上限 clamp 在 90
ACESTEP_DURATION_SEC = float(os.getenv("ACESTEP_DURATION_SEC", "45"))
ACESTEP_INFERENCE_STEPS = int(os.getenv("ACESTEP_INFERENCE_STEPS", "8"))

_default_remote = "https://tactually-venerable-inez.ngrok-free.dev"
ACESTEP_REMOTE_URLS: List[str] = [
    u.strip().rstrip("/")
    for u in os.getenv("ACESTEP_REMOTE_URLS", _default_remote).split(",")
    if u.strip()
]

ProgressCb = Optional[Callable[[int, str], None]]

SINGER_PROMPTS: Dict[str, str] = {
    "female_bright": (
        "A bright Mandarin pop travel song. The lead female vocal is clear, present, and sings "
        "Chinese lyrics throughout the verse and chorus; sunny coastal vibe with full-band backing."
    ),
    "female_warm": (
        "A warm Mandarin pop ballad. The lead female vocal is soft but clearly audible, singing "
        "Chinese lyrics in verse and chorus; nostalgic travel mood with full-band accompaniment."
    ),
    "female_soft": (
        "A gentle Mandarin acoustic pop song. Intimate soft female lead vocals sing Chinese lyrics "
        "prominently; light seaside arrangement that never covers the singer."
    ),
    "male_deep": (
        "A grounded Mandarin pop anthem. Deep rich male lead vocals sing Chinese lyrics clearly "
        "through verse and chorus; full-band travel mood."
    ),
    "male_warm": (
        "A heartfelt Mandarin pop song. Warm mid-range male lead vocals are front and center, "
        "singing Chinese lyrics in verse and chorus; coastal journey arrangement."
    ),
    "male_clear": (
        "An uplifting Mandarin pop souvenir song. Clear strong male lead vocals sing Chinese lyrics "
        "prominently; memorable travel arrangement with full band."
    ),
}


def _headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "ngrok-skip-browser-warning": "1",
    }
    if ACESTEP_API_KEY:
        h["Authorization"] = f"Bearer {ACESTEP_API_KEY}"
    if extra:
        h.update(extra)
    return h


def local_status(timeout: float = 1.5) -> Dict[str, Any]:
    """
    本機 ACE-Step :8001 狀態（含 5Hz LM 是否真的載入）。
    官方 health 會回 llm_initialized / loaded_lm_model。
    """
    out: Dict[str, Any] = {
        "ok": False,
        "url": ACESTEP_URL,
        "models_initialized": False,
        "llm_initialized": False,
        "loaded_model": None,
        "loaded_lm_model": None,
        "thinking_requested": ACESTEP_THINKING,
        "thinking_effective": False,
        "shift": ACESTEP_SHIFT,
        "production_caption": ACESTEP_PRODUCTION_CAPTION,
        "error": None,
    }
    try:
        r = requests.get(f"{ACESTEP_URL}/health", timeout=timeout)
        if r.status_code != 200:
            out["error"] = f"HTTP {r.status_code}"
            return out
        wrap = r.json()
        if isinstance(wrap, dict) and "code" in wrap and int(wrap.get("code") or 0) != 200:
            out["error"] = str(wrap.get("error") or wrap)
            return out
        data = wrap.get("data") if isinstance(wrap, dict) and "data" in wrap else wrap
        if not isinstance(data, dict):
            out["ok"] = True
            return out
        out["ok"] = True
        out["models_initialized"] = bool(data.get("models_initialized"))
        out["llm_initialized"] = bool(data.get("llm_initialized"))
        out["loaded_model"] = data.get("loaded_model")
        lm = data.get("loaded_lm_model")
        out["loaded_lm_model"] = lm
        lm_name = str(lm or "").strip().lower()
        lm_ok = bool(data.get("llm_initialized")) and lm_name not in ("", "none", "null", "no lm")
        out["thinking_effective"] = bool(ACESTEP_THINKING and lm_ok)
        if ACESTEP_THINKING and not lm_ok:
            out["error"] = "thinking=true 但 5Hz LM 未載入（No LM）"
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


def local_available(timeout: float = 1.5) -> bool:
    """只檢查本機 ACE-Step :8001（給 /acestep/health 用，避免遠端遞迴）。"""
    return bool(local_status(timeout=timeout).get("ok"))


def remote_available(timeout: float = 3.0) -> bool:
    for base in ACESTEP_REMOTE_URLS:
        try:
            r = requests.get(
                f"{base}/acestep/health",
                headers={"ngrok-skip-browser-warning": "1"},
                timeout=timeout,
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data.get("ok"):
                    return True
        except Exception:
            continue
    return False


def is_available(timeout: float = 1.5) -> bool:
    return local_available(timeout=timeout) or remote_available(timeout=max(timeout, 2.5))


def format_lyrics(lyrics: dict) -> str:
    """ACE-Step 建議結構標籤；避免空歌詞被當成 instrumental。"""
    title = (lyrics.get("title") or "").strip()
    verse = (lyrics.get("verse") or "").strip()
    chorus = (lyrics.get("chorus") or "").strip()
    parts = []
    if title:
        parts.append(f"[Intro]\n{title}")
    if verse:
        parts.append(f"[Verse]\n{verse}")
    if chorus:
        parts.append(f"[Chorus]\n{chorus}")
        # 再唱一次副歌，拉長人聲段落
        parts.append(f"[Chorus]\n{chorus}")
    text = "\n\n".join(parts).strip()
    if not text:
        return ""
    return text


def format_key_scale(key: Optional[str]) -> str:
    raw = (key or "").strip()
    if not raw:
        return ""
    if " " in raw:
        return raw
    if raw.endswith("m") and not raw.lower().endswith("major"):
        return f"{raw[:-1]} Minor"
    return f"{raw} Major"


def build_prompt(
    *,
    singer_id: Optional[str],
    engine_style: Optional[str],
    title: Optional[str] = None,
    material: Optional[dict] = None,
    bpm: Optional[float] = None,
    key: Optional[str] = None,
) -> str:
    """預設走 music_director 完整編曲 caption；ACESTEP_PRODUCTION_CAPTION=0 可回舊短 prompt。"""
    from app.voice import music_director as _dir

    if not ACESTEP_PRODUCTION_CAPTION:
        return _dir.build_legacy_prompt(
            singer_id=singer_id,
            engine_style=engine_style,
            title=title,
            material=material,
        )
    return _dir.build_production_caption(
        singer_id=singer_id,
        engine_style=engine_style,
        title=title,
        material=material,
        bpm=bpm,
        key=key,
    )


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
            return _parse_result_payload(json.loads(raw))
        except Exception:
            return None
    return None


def _audio_url_from_result(item: dict, base_url: str) -> Optional[str]:
    file_ref = item.get("file") or item.get("audio") or item.get("path")
    if not file_ref:
        return None
    file_ref = str(file_ref)
    if file_ref.startswith("http://") or file_ref.startswith("https://"):
        return file_ref
    if file_ref.startswith("/"):
        return f"{base_url.rstrip('/')}{file_ref}"
    return f"{base_url.rstrip('/')}/v1/audio?path={urllib.parse.quote(file_ref, safe='')}"


def _generate_via_local_api(
    *,
    lyrics: dict,
    bpm: float,
    key: Optional[str],
    singer_id: Optional[str],
    engine_style: Optional[str],
    duration_sec: float,
    out_path: Path,
    progress: ProgressCb = None,
    stats: Optional[Dict[str, Any]] = None,
    material: Optional[dict] = None,
) -> Path:
    t0 = time.time()
    if progress:
        progress(20, "連線 AI 唱歌引擎")

    lyric_text = format_lyrics(lyrics)
    if not lyric_text:
        raise RuntimeError("歌詞為空，無法產生 AI 人聲版")

    prompt = build_prompt(
        singer_id=singer_id,
        engine_style=engine_style,
        title=lyrics.get("title"),
        material=material,
        bpm=bpm,
        key=key,
    )
    duration_sec = max(20.0, min(90.0, float(duration_sec)))
    bpm_i = int(max(30, min(300, round(float(bpm))))) if bpm else None
    inference_steps = int(ACESTEP_INFERENCE_STEPS)

    body: Dict[str, Any] = {
        "prompt": prompt,
        "caption": prompt,
        "lyrics": lyric_text,
        "vocal_language": "zh",
        "thinking": ACESTEP_THINKING,
        "use_cot_caption": False,
        "use_cot_language": False,
        "use_cot_metas": False,
        "instrumental": False,
        "audio_format": "mp3" if str(out_path).lower().endswith(".mp3") else "wav",
        "model": ACESTEP_MODEL,
        "audio_duration": duration_sec,
        "time_signature": "4",
        "batch_size": 1,
        "inference_steps": inference_steps,
    }
    if ACESTEP_SHIFT is not None:
        body["shift"] = float(ACESTEP_SHIFT)
    if bpm_i:
        body["bpm"] = bpm_i
    key_scale = format_key_scale(key)
    if key_scale:
        body["key_scale"] = key_scale
    if ACESTEP_API_KEY:
        body["ai_token"] = ACESTEP_API_KEY

    lm_status = local_status(timeout=2.0)
    print(
        "[acestep] generate "
        f"model={ACESTEP_MODEL} steps={inference_steps} shift={ACESTEP_SHIFT} "
        f"thinking={ACESTEP_THINKING} thinking_effective={lm_status.get('thinking_effective')} "
        f"lm={lm_status.get('loaded_lm_model')} caption_chars={len(prompt)} "
        f"production_caption={ACESTEP_PRODUCTION_CAPTION}"
    )
    if ACESTEP_THINKING and not lm_status.get("thinking_effective"):
        print(
            "[acestep] WARN thinking=true but 5Hz LM not loaded — "
            f"{lm_status.get('error') or lm_status.get('loaded_lm_model')}"
        )

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
        try:
            qr = requests.post(
                f"{ACESTEP_URL}/query_result",
                headers=_headers(),
                json={"task_id_list": [task_id]},
                # 首次載入 LM／推理時 worker 會卡住，query 也需較長讀取逾時
                timeout=120,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if progress:
                progress(50, "AI 正在作曲唱歌")
            print(f"[acestep] query_result retry after {type(e).__name__}")
            time.sleep(ACESTEP_POLL_INTERVAL)
            continue
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
            audio_url = _audio_url_from_result(item, ACESTEP_URL)
            if not audio_url:
                raise RuntimeError(f"ACE-Step 結果缺少音檔: {item}")
            if progress:
                progress(85, "下載成品")
            ar = requests.get(audio_url, headers=_headers(), timeout=120)
            if ar.status_code >= 400:
                raise RuntimeError(f"ACE-Step 下載音檔失敗 HTTP {ar.status_code}")
            if len(ar.content) < 5000:
                raise RuntimeError("ACE-Step 音檔異常過短")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(ar.content)
            if progress:
                progress(95, "輸出成品")
            if stats is not None:
                stats.update(
                    {
                        "engine": "acestep",
                        "model": ACESTEP_MODEL,
                        "via": "local",
                        "duration_sec": float(duration_sec),
                        "inference_steps": int(inference_steps),
                        "shift": ACESTEP_SHIFT,
                        "thinking": ACESTEP_THINKING,
                        "thinking_effective": bool(lm_status.get("thinking_effective")),
                        "loaded_lm_model": lm_status.get("loaded_lm_model"),
                        "production_caption": ACESTEP_PRODUCTION_CAPTION,
                        "caption_chars": len(prompt),
                        "seed": item.get("seed") or item.get("seeds"),
                        "elapsed_ms": int((time.time() - t0) * 1000),
                    }
                )
            return out_path
        if status == 2:
            err = row.get("error") or row.get("message") or row.get("result") or "unknown"
            raise RuntimeError(f"ACE-Step 產生失敗: {err}")

        if progress:
            progress(55, "AI 正在作曲唱歌")
        time.sleep(ACESTEP_POLL_INTERVAL)

    raise RuntimeError(f"ACE-Step 逾時（status={last_status}）")


def _generate_via_remote(
    *,
    lyrics: dict,
    bpm: float,
    key: Optional[str],
    singer_id: Optional[str],
    engine_style: Optional[str],
    duration_sec: float,
    out_path: Path,
    progress: ProgressCb = None,
    stats: Optional[Dict[str, Any]] = None,
    material: Optional[dict] = None,
) -> Path:
    t0 = time.time()
    duration_sec = max(20.0, min(90.0, float(duration_sec)))
    inference_steps = int(ACESTEP_INFERENCE_STEPS)
    if progress:
        progress(20, "連線本機 AI 唱歌引擎")
    payload = {
        "lyrics": lyrics,
        "bpm": bpm,
        "key": key,
        "singer_id": singer_id,
        "engine_style": engine_style,
        "duration_sec": duration_sec,
        "material": material or {},
    }
    last_err = None
    for base in ACESTEP_REMOTE_URLS:
        try:
            if progress:
                progress(35, "AI 正在作曲唱歌")
            r = requests.post(
                f"{base}/acestep/generate",
                headers=_headers(),
                json=payload,
                timeout=ACESTEP_TIMEOUT_SEC + 30,
            )
            if r.status_code >= 400:
                last_err = f"HTTP {r.status_code}: {r.text[:240]}"
                continue
            ctype = (r.headers.get("content-type") or "").lower()
            if "json" in ctype:
                last_err = f"unexpected json: {r.text[:240]}"
                continue
            if len(r.content) < 5000:
                last_err = "remote audio too small"
                continue
            if progress:
                progress(90, "輸出成品")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(r.content)
            if stats is not None:
                stats.update(
                    {
                        "engine": "acestep",
                        "model": ACESTEP_MODEL,
                        "via": "remote",
                        "duration_sec": float(duration_sec),
                        "inference_steps": int(inference_steps),
                        "shift": ACESTEP_SHIFT,
                        "thinking": ACESTEP_THINKING,
                        "production_caption": ACESTEP_PRODUCTION_CAPTION,
                        "elapsed_ms": int((time.time() - t0) * 1000),
                    }
                )
            return out_path
        except Exception as e:
            last_err = str(e)
            continue
    raise RuntimeError(f"遠端 ACE-Step 失敗: {last_err or 'unreachable'}")


def generate_to_file(
    *,
    lyrics: dict,
    bpm: float,
    key: Optional[str],
    singer_id: Optional[str] = None,
    engine_style: Optional[str] = None,
    duration_sec: float = ACESTEP_DURATION_SEC,
    out_path: Path,
    progress: ProgressCb = None,
    force_local: bool = False,
    stats: Optional[Dict[str, Any]] = None,
    material: Optional[dict] = None,
) -> Path:
    """
    產生整曲並寫入 out_path。
    force_local=True 時只打本機 :8001（給 /acestep/generate 代理用）。
    若傳入 stats=dict，會寫入 duration_sec / inference_steps / elapsed_ms 等計量欄位。
    """
    if force_local or local_available():
        return _generate_via_local_api(
            lyrics=lyrics,
            bpm=bpm,
            key=key,
            singer_id=singer_id,
            engine_style=engine_style,
            duration_sec=duration_sec,
            out_path=out_path,
            progress=progress,
            stats=stats,
            material=material,
        )
    if ACESTEP_REMOTE_URLS:
        return _generate_via_remote(
            lyrics=lyrics,
            bpm=bpm,
            key=key,
            singer_id=singer_id,
            engine_style=engine_style,
            duration_sec=duration_sec,
            out_path=out_path,
            progress=progress,
            stats=stats,
            material=material,
        )
    raise RuntimeError("ACE-Step 未啟動（本機 :8001 無回應）")


def generate_to_tempfile(**kwargs) -> str:
    """給 HTTP 代理用：寫到暫存 mp3，回傳路徑。"""
    fd, path = tempfile.mkstemp(prefix="acestep_", suffix=".mp3")
    os.close(fd)
    out = Path(path)
    try:
        generate_to_file(out_path=out, force_local=True, **kwargs)
        return str(out)
    except Exception:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
        raise
