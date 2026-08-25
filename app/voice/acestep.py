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
ACESTEP_MODEL = os.getenv("ACESTEP_MODEL", "acestep-v15-xl-turbo")
# thinking=true 用 5Hz LM 規劃，人聲明顯較穩；需本機 ACE-Step 已載入 LM
ACESTEP_THINKING = os.getenv("ACESTEP_THINKING", "1").strip().lower() not in ("0", "false", "no")
# Turbo／XL-turbo 官方建議 shift=3.0；設為 off/none 則不傳（A/B 對照用）
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
# XL turbo 仍建議 8；若改 xl-sft 可設 ACESTEP_INFERENCE_STEPS=50
_default_steps = "8"
if "sft" in ACESTEP_MODEL and "turbo" not in ACESTEP_MODEL:
    _default_steps = "50"
ACESTEP_INFERENCE_STEPS = int(os.getenv("ACESTEP_INFERENCE_STEPS", _default_steps))
# Sprint 2：一次產兩版；XL+4B 記憶體吃緊可設 1
ACESTEP_BATCH_SIZE = max(1, min(8, int(os.getenv("ACESTEP_BATCH_SIZE", "2"))))
# 先 lossless 再母帶／轉 MP3
ACESTEP_AUDIO_FORMAT = (os.getenv("ACESTEP_AUDIO_FORMAT", "wav") or "wav").strip().lower()
if ACESTEP_AUDIO_FORMAT not in ("wav", "flac"):
    ACESTEP_AUDIO_FORMAT = "wav"
ACESTEP_LM_MODEL = os.getenv("ACESTEP_LM_MODEL", "acestep-5Hz-lm-4B")

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


def remote_status(timeout: float = 3.0) -> Dict[str, Any]:
    """經 ngrok 探測本機 Automusic /acestep/health（給 Zeabur health 用）。"""
    out: Dict[str, Any] = {
        "ok": False,
        "via": "remote",
        "url": None,
        "loaded_model": None,
        "loaded_lm_model": None,
        "llm_initialized": False,
        "thinking_requested": ACESTEP_THINKING,
        "thinking_effective": False,
        "shift": ACESTEP_SHIFT,
        "production_caption": ACESTEP_PRODUCTION_CAPTION,
        "error": None,
    }
    for base in ACESTEP_REMOTE_URLS:
        try:
            r = requests.get(
                f"{base}/acestep/health",
                headers={"ngrok-skip-browser-warning": "1"},
                timeout=timeout,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            if not isinstance(data, dict) or not data.get("ok"):
                continue
            out["ok"] = True
            out["url"] = base
            out["loaded_model"] = data.get("loaded_model")
            out["loaded_lm_model"] = data.get("loaded_lm_model")
            out["llm_initialized"] = bool(data.get("llm_initialized"))
            out["thinking_effective"] = bool(data.get("thinking_effective"))
            if data.get("shift") is not None:
                out["shift"] = data.get("shift")
            if data.get("production_caption") is not None:
                out["production_caption"] = data.get("production_caption")
            out["error"] = data.get("error")
            return out
        except Exception as e:
            out["error"] = str(e)
            continue
    return out


def remote_available(timeout: float = 3.0) -> bool:
    return bool(remote_status(timeout=timeout).get("ok"))


def is_available(timeout: float = 1.5) -> bool:
    return local_available(timeout=timeout) or remote_available(timeout=max(timeout, 2.5))


def format_lyrics(lyrics: dict) -> str:
    """ACE-Step 結構：Intro → Verse → Pre-Chorus → Chorus → Outro（45 秒好唱）。"""
    title = (lyrics.get("title") or "").strip()
    verse = (lyrics.get("verse") or "").strip()
    pre = (lyrics.get("prechorus") or lyrics.get("pre_chorus") or "").strip()
    chorus = (lyrics.get("chorus") or "").strip()
    parts = []
    if title:
        parts.append(f"[Intro]\n{title}")
    if verse:
        parts.append(f"[Verse]\n{verse}")
    if pre:
        parts.append(f"[Pre-Chorus]\n{pre}")
    if chorus:
        parts.append(f"[Chorus]\n{chorus}")
        # 短 outro：副歌末句或歌名，避免再整段副歌佔滿 45 秒
        outro_line = chorus.strip().split("\n")[-1].strip() if chorus.strip() else title
        if outro_line:
            parts.append(f"[Outro]\n{outro_line}")
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
    route_id: Optional[str] = None,
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
        route_id=route_id,
    )


def _parse_result_items(raw: Any) -> List[dict]:
    """ACE batch 成功時 result 為 list[{file, seed, ...}]；單首也可能是 dict。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if isinstance(raw, dict):
        for key in ("audios", "items", "results", "data"):
            nested = raw.get(key)
            if isinstance(nested, list):
                return [x for x in nested if isinstance(x, dict)]
        if raw.get("file") or raw.get("audio") or raw.get("path") or raw.get("url"):
            return [raw]
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    return []


def _parse_result_payload(raw: Any) -> Optional[dict]:
    items = _parse_result_items(raw)
    return items[0] if items else None


def _audio_url_from_result(item: dict, base_url: str) -> Optional[str]:
    file_ref = (
        item.get("url")
        or item.get("file")
        or item.get("audio")
        or item.get("path")
    )
    if not file_ref:
        return None
    file_ref = str(file_ref)
    if file_ref.startswith("http://") or file_ref.startswith("https://"):
        return file_ref
    if file_ref.startswith("/"):
        return f"{base_url.rstrip('/')}{file_ref}"
    return f"{base_url.rstrip('/')}/v1/audio?path={urllib.parse.quote(file_ref, safe='')}"


def _ext_for_format(audio_format: str) -> str:
    fmt = (audio_format or "wav").lower()
    if fmt == "flac":
        return ".flac"
    if fmt == "mp3":
        return ".mp3"
    return ".wav"


def _generate_via_local_api(
    *,
    lyrics: dict,
    bpm: float,
    key: Optional[str],
    singer_id: Optional[str],
    engine_style: Optional[str],
    duration_sec: float,
    out_dir: Path,
    batch_size: int = 1,
    audio_format: str = ACESTEP_AUDIO_FORMAT,
    progress: ProgressCb = None,
    stats: Optional[Dict[str, Any]] = None,
    material: Optional[dict] = None,
    route_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """呼叫本機 ACE release_task；下載 batch 結果為 lossless 檔，回傳 candidates。"""
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
        route_id=route_id,
    )
    duration_sec = max(20.0, min(90.0, float(duration_sec)))
    bpm_i = int(max(30, min(300, round(float(bpm))))) if bpm else None
    inference_steps = int(ACESTEP_INFERENCE_STEPS)
    batch_size = max(1, min(8, int(batch_size or 1)))
    fmt = (audio_format or ACESTEP_AUDIO_FORMAT).lower()
    if fmt not in ("wav", "flac", "mp3"):
        fmt = "wav"
    ext = _ext_for_format(fmt)

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
        "audio_format": fmt,
        "model": ACESTEP_MODEL,
        "audio_duration": duration_sec,
        "time_signature": "4",
        "batch_size": batch_size,
        "inference_steps": inference_steps,
        "use_random_seed": True,
        "allow_lm_batch": batch_size >= 2,
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
        f"batch={batch_size} format={fmt} "
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
            items = _parse_result_items(row.get("result"))
            if not items:
                raise RuntimeError("ACE-Step 成功但結果為空")
            if progress:
                progress(85, "下載成品")
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            candidates: List[Dict[str, Any]] = []
            seeds: List[Any] = []
            for i, item in enumerate(items[:batch_size]):
                audio_url = _audio_url_from_result(item, ACESTEP_URL)
                if not audio_url:
                    raise RuntimeError(f"ACE-Step 結果缺少音檔: {item}")
                ar = requests.get(audio_url, headers=_headers(), timeout=120)
                if ar.status_code >= 400:
                    raise RuntimeError(f"ACE-Step 下載音檔失敗 HTTP {ar.status_code}")
                if len(ar.content) < 5000:
                    raise RuntimeError("ACE-Step 音檔異常過短")
                label = "a" if i == 0 else "b" if i == 1 else str(i)
                name = f"candidate_{label}{ext}"
                dest = out_dir / name
                dest.write_bytes(ar.content)
                seed = item.get("seed")
                seeds.append(seed)
                candidates.append(
                    {
                        "id": label,
                        "index": i,
                        "file": name,
                        "path": str(dest),
                        "seed": seed,
                        "audio_format": fmt,
                    }
                )
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
                        "batch_size": len(candidates),
                        "audio_format": fmt,
                        "seed": seeds[0] if len(seeds) == 1 else seeds,
                        "seeds": seeds,
                        "elapsed_ms": int((time.time() - t0) * 1000),
                    }
                )
            return candidates
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
    out_dir: Path,
    batch_size: int = 1,
    audio_format: str = ACESTEP_AUDIO_FORMAT,
    progress: ProgressCb = None,
    stats: Optional[Dict[str, Any]] = None,
    material: Optional[dict] = None,
    route_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """經 ngrok 打本機 /acestep/generate；batch>1 時回 ZIP。"""
    import io
    import zipfile

    t0 = time.time()
    duration_sec = max(20.0, min(90.0, float(duration_sec)))
    inference_steps = int(ACESTEP_INFERENCE_STEPS)
    batch_size = max(1, min(8, int(batch_size or 1)))
    fmt = (audio_format or ACESTEP_AUDIO_FORMAT).lower()
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
        "batch_size": batch_size,
        "audio_format": fmt,
        "route_id": route_id,
    }
    last_err = None
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
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
            candidates: List[Dict[str, Any]] = []
            seeds: List[Any] = []
            if "zip" in ctype or (len(r.content) >= 4 and r.content[:2] == b"PK"):
                if progress:
                    progress(90, "解壓成品")
                with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                    manifest = {}
                    if "manifest.json" in zf.namelist():
                        try:
                            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                        except Exception:
                            manifest = {}
                    names = sorted(
                        n
                        for n in zf.namelist()
                        if n.startswith("candidate_")
                        and not n.endswith("/")
                    )
                    for i, name in enumerate(names):
                        data = zf.read(name)
                        if len(data) < 5000:
                            continue
                        dest = out_dir / Path(name).name
                        dest.write_bytes(data)
                        label = Path(name).stem.replace("candidate_", "") or str(i)
                        seed = None
                        if isinstance(manifest.get("candidates"), list) and i < len(manifest["candidates"]):
                            seed = manifest["candidates"][i].get("seed")
                        seeds.append(seed)
                        candidates.append(
                            {
                                "id": label,
                                "index": i,
                                "file": dest.name,
                                "path": str(dest),
                                "seed": seed,
                                "audio_format": fmt,
                            }
                        )
            else:
                if "json" in ctype:
                    last_err = f"unexpected json: {r.text[:240]}"
                    continue
                if len(r.content) < 5000:
                    last_err = "remote audio too small"
                    continue
                if progress:
                    progress(90, "輸出成品")
                ext = _ext_for_format(fmt if fmt != "mp3" else "mp3")
                # 遠端單檔可能已是 mp3（舊協定）
                if "mpeg" in ctype or "mp3" in ctype:
                    ext = ".mp3"
                    fmt = "mp3"
                dest = out_dir / f"candidate_a{ext}"
                dest.write_bytes(r.content)
                candidates.append(
                    {
                        "id": "a",
                        "index": 0,
                        "file": dest.name,
                        "path": str(dest),
                        "seed": None,
                        "audio_format": fmt,
                    }
                )
            if not candidates:
                last_err = "remote returned no audio candidates"
                continue
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
                        "batch_size": len(candidates),
                        "audio_format": fmt,
                        "seed": seeds[0] if len(seeds) == 1 else (seeds or None),
                        "seeds": seeds,
                        "elapsed_ms": int((time.time() - t0) * 1000),
                    }
                )
            return candidates
        except Exception as e:
            last_err = str(e)
            continue
    raise RuntimeError(f"遠端 ACE-Step 失敗: {last_err or 'unreachable'}")


def generate_candidates(
    *,
    lyrics: dict,
    bpm: float,
    key: Optional[str],
    singer_id: Optional[str] = None,
    engine_style: Optional[str] = None,
    duration_sec: float = ACESTEP_DURATION_SEC,
    out_dir: Path,
    batch_size: Optional[int] = None,
    audio_format: Optional[str] = None,
    progress: ProgressCb = None,
    force_local: bool = False,
    stats: Optional[Dict[str, Any]] = None,
    material: Optional[dict] = None,
    route_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    產生 1～N 首候選（預設 batch=ACESTEP_BATCH_SIZE），寫入 out_dir。
    成功回傳 [{id,index,file,path,seed,audio_format}, ...]。
    batch≥2 若 OOM／失敗會自動退回 batch=1 再試一次。
    """
    bs = int(batch_size if batch_size is not None else ACESTEP_BATCH_SIZE)
    bs = max(1, min(8, bs))
    fmt = (audio_format or ACESTEP_AUDIO_FORMAT).lower()

    def _once(n: int) -> List[Dict[str, Any]]:
        kwargs = dict(
            lyrics=lyrics,
            bpm=bpm,
            key=key,
            singer_id=singer_id,
            engine_style=engine_style,
            duration_sec=duration_sec,
            out_dir=out_dir,
            batch_size=n,
            audio_format=fmt,
            progress=progress,
            stats=stats,
            material=material,
            route_id=route_id,
        )
        if force_local or local_available():
            return _generate_via_local_api(**kwargs)
        if ACESTEP_REMOTE_URLS:
            return _generate_via_remote(**kwargs)
        raise RuntimeError("ACE-Step 未啟動（本機 :8001 無回應）")

    try:
        return _once(bs)
    except Exception as e:
        msg = str(e).lower()
        if bs > 1 and any(k in msg for k in ("oom", "out of memory", "cuda", "mlx", "memory")):
            print(f"[acestep] batch={bs} failed ({e}); retry batch=1")
            if stats is not None:
                stats.clear()
            return _once(1)
        raise


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
    batch_size: int = 1,
) -> Path:
    """
    產生整曲並寫入 out_path（相容舊介面；預設單首）。
    旅程成品請改用 generate_candidates。
    """
    out_path = Path(out_path)
    tmp = Path(tempfile.mkdtemp(prefix="acestep_one_"))
    try:
        cands = generate_candidates(
            lyrics=lyrics,
            bpm=bpm,
            key=key,
            singer_id=singer_id,
            engine_style=engine_style,
            duration_sec=duration_sec,
            out_dir=tmp,
            batch_size=batch_size,
            audio_format="mp3" if str(out_path).lower().endswith(".mp3") else ACESTEP_AUDIO_FORMAT,
            progress=progress,
            force_local=force_local,
            stats=stats,
            material=material,
        )
        if not cands:
            raise RuntimeError("ACE-Step 未產出音檔")
        src = Path(cands[0]["path"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(src.read_bytes())
        return out_path
    finally:
        try:
            for p in tmp.glob("*"):
                p.unlink(missing_ok=True)
            tmp.rmdir()
        except OSError:
            pass


def generate_batch_package(
    *,
    lyrics: dict,
    bpm: float,
    key: Optional[str] = None,
    singer_id: Optional[str] = None,
    engine_style: Optional[str] = None,
    duration_sec: float = ACESTEP_DURATION_SEC,
    batch_size: Optional[int] = None,
    audio_format: Optional[str] = None,
    material: Optional[dict] = None,
    route_id: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    給 /acestep/generate 代理：batch=1 回單一 mp3；batch>1 回 zip（內含 wav/flac + manifest）。
    回傳 {kind: 'mp3'|'zip', path: str, candidates: list}
    """
    import zipfile

    from app.main import compress_to_mp3

    bs = int(batch_size if batch_size is not None else ACESTEP_BATCH_SIZE)
    bs = max(1, min(8, bs))
    fmt = (audio_format or ACESTEP_AUDIO_FORMAT).lower()
    tmp = Path(tempfile.mkdtemp(prefix="acestep_pkg_"))
    try:
        cands = generate_candidates(
            lyrics=lyrics,
            bpm=bpm,
            key=key,
            singer_id=singer_id,
            engine_style=engine_style,
            duration_sec=duration_sec,
            out_dir=tmp,
            batch_size=bs,
            audio_format=fmt,
            force_local=True,
            stats=stats,
            material=material,
            route_id=route_id,
        )
        if not cands:
            raise RuntimeError("ACE-Step 未產出音檔")
        if len(cands) == 1:
            src = Path(cands[0]["path"])
            if src.suffix.lower() == ".mp3":
                return {"kind": "mp3", "path": str(src), "candidates": cands, "tmpdir": str(tmp)}
            mp3 = compress_to_mp3(str(src))
            if not mp3:
                # 壓不出就直接給 lossless
                return {
                    "kind": "audio",
                    "path": str(src),
                    "media_type": "audio/wav" if src.suffix.lower() == ".wav" else "audio/flac",
                    "candidates": cands,
                    "tmpdir": str(tmp),
                }
            return {"kind": "mp3", "path": mp3, "candidates": cands, "tmpdir": str(tmp)}

        zip_path = tmp / "candidates.zip"
        manifest = {
            "batch_size": len(cands),
            "audio_format": fmt,
            "candidates": [
                {"id": c.get("id"), "file": c.get("file"), "seed": c.get("seed")}
                for c in cands
            ],
        }
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            for c in cands:
                zf.write(c["path"], arcname=c["file"])
        return {"kind": "zip", "path": str(zip_path), "candidates": cands, "tmpdir": str(tmp)}
    except Exception:
        try:
            for p in tmp.rglob("*"):
                if p.is_file():
                    p.unlink(missing_ok=True)
            tmp.rmdir()
        except OSError:
            pass
        raise


def generate_to_tempfile(**kwargs) -> str:
    """給 HTTP 代理用：寫到暫存 mp3，回傳路徑（單首）。"""
    batch = int(kwargs.pop("batch_size", 1) or 1)
    pkg = generate_batch_package(batch_size=max(1, batch), **kwargs)
    return str(pkg["path"])
