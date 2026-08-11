"""
本機 ACE-Step 1.5 API 客戶端。

預設以 journey 的 MIDI 伴奏 preview 做 cover（鎖旋律／結構），再長人聲。
- 本機：multipart → http://127.0.0.1:8001/release_task
- 雲端 Zeabur：multipart → ngrok /acestep/generate
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

ACESTEP_URL = os.getenv("ACESTEP_URL", "http://127.0.0.1:8001").rstrip("/")
ACESTEP_API_KEY = (os.getenv("ACESTEP_API_KEY") or "").strip()
ACESTEP_MODEL = os.getenv("ACESTEP_MODEL", "acestep-v15-turbo")
ACESTEP_THINKING = os.getenv("ACESTEP_THINKING", "1").strip().lower() not in ("0", "false", "no")
ACESTEP_COVER_STRENGTH = float(os.getenv("ACESTEP_COVER_STRENGTH", "0.85"))
ACESTEP_POLL_INTERVAL = float(os.getenv("ACESTEP_POLL_INTERVAL", "2.0"))
ACESTEP_TIMEOUT_SEC = float(os.getenv("ACESTEP_TIMEOUT_SEC", "900"))

TEASER_DURATION_SEC = float(os.getenv("ACESTEP_TEASER_DURATION", "45"))
FULL_DURATION_SEC = float(os.getenv("ACESTEP_FULL_DURATION", "105"))

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
        "Chinese lyrics throughout the verse and chorus; sunny coastal vibe with full-band backing. "
        "Keep the source melody and chord progression."
    ),
    "female_warm": (
        "A warm Mandarin pop ballad. The lead female vocal is soft but clearly audible, singing "
        "Chinese lyrics in verse and chorus; nostalgic travel mood. "
        "Keep the source melody and chord progression."
    ),
    "female_soft": (
        "A gentle Mandarin acoustic pop song. Intimate soft female lead vocals sing Chinese lyrics "
        "prominently; light seaside arrangement. Keep the source melody and chord progression."
    ),
    "male_deep": (
        "A grounded Mandarin pop anthem. Deep rich male lead vocals sing Chinese lyrics clearly "
        "through verse and chorus. Keep the source melody and chord progression."
    ),
    "male_warm": (
        "A heartfelt Mandarin pop song. Warm mid-range male lead vocals are front and center, "
        "singing Chinese lyrics. Keep the source melody and chord progression."
    ),
    "male_clear": (
        "An uplifting Mandarin pop souvenir song. Clear strong male lead vocals sing Chinese lyrics "
        "prominently. Keep the source melody and chord progression."
    ),
}


@dataclass
class GenerateResult:
    path: Path
    seed: Optional[str] = None
    duration_sec: Optional[float] = None
    engine: str = "acestep_cover"


def _headers(json_body: bool = True, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = {
        "Accept": "application/json",
        "ngrok-skip-browser-warning": "1",
    }
    if json_body:
        h["Content-Type"] = "application/json"
    if ACESTEP_API_KEY:
        h["Authorization"] = f"Bearer {ACESTEP_API_KEY}"
    if extra:
        h.update(extra)
    return h


def local_available(timeout: float = 1.5) -> bool:
    try:
        r = requests.get(f"{ACESTEP_URL}/health", timeout=timeout)
        if r.status_code != 200:
            return False
        data = r.json()
        if isinstance(data, dict) and "code" in data:
            return int(data.get("code") or 0) == 200
        return True
    except Exception:
        return False


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


def format_lyrics(lyrics: dict, *, full: bool = False) -> str:
    """試聽：Intro+Verse+Chorus；完整：再重複主副歌拉長結構。"""
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
    if full:
        if verse:
            parts.append(f"[Verse]\n{verse}")
        if chorus:
            parts.append(f"[Chorus]\n{chorus}")
            parts.append(f"[Chorus]\n{chorus}")
    elif chorus:
        parts.append(f"[Chorus]\n{chorus}")
    return "\n\n".join(parts).strip()


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
    extend: bool = False,
) -> str:
    base = SINGER_PROMPTS.get(
        singer_id or "",
        "Mandarin pop with natural lead vocals singing Chinese lyrics; keep source melody and chords",
    )
    bits = [
        base,
        "cover the source accompaniment",
        "preserve the original melody contour and rhythm",
        "same melody and harmonic progression as the source audio",
        "add clearly audible lead singer vocals on top",
        "not instrumental-only",
        "do not invent a new unrelated melody",
    ]
    if extend:
        bits.extend([
            "extend into a fuller song with verse and chorus",
            "keep the same travel melody while developing longer structure",
            "about two minutes",
        ])
    if engine_style:
        bits.append(str(engine_style))
    if title:
        bits.append(f"song about: {title}")
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


def _extract_seed(item: dict) -> Optional[str]:
    seed = item.get("seed_value") or item.get("seed")
    if seed is None:
        return None
    return str(seed)


def _poll_and_download(
    task_id: str,
    out_path: Path,
    *,
    progress: ProgressCb = None,
    duration_sec: float,
) -> GenerateResult:
    deadline = time.time() + ACESTEP_TIMEOUT_SEC
    last_status = None
    while time.time() < deadline:
        try:
            qr = requests.post(
                f"{ACESTEP_URL}/query_result",
                headers=_headers(json_body=True),
                json={"task_id_list": [task_id]},
                timeout=120,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if progress:
                progress(50, "AI 正在依旋律唱歌")
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
            ar = requests.get(audio_url, headers=_headers(json_body=False), timeout=120)
            if ar.status_code >= 400:
                raise RuntimeError(f"ACE-Step 下載音檔失敗 HTTP {ar.status_code}")
            if len(ar.content) < 5000:
                raise RuntimeError("ACE-Step 音檔異常過短")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(ar.content)
            if progress:
                progress(95, "輸出成品")
            return GenerateResult(
                path=out_path,
                seed=_extract_seed(item),
                duration_sec=duration_sec,
                engine="acestep_cover",
            )
        if status == 2:
            err = row.get("error") or row.get("message") or row.get("result") or "unknown"
            raise RuntimeError(f"ACE-Step 產生失敗: {err}")

        if progress:
            progress(55, "AI 正在依旋律唱歌")
        time.sleep(ACESTEP_POLL_INTERVAL)

    raise RuntimeError(f"ACE-Step 逾時（status={last_status}）")


def _generate_via_local_api(
    *,
    lyrics: dict,
    bpm: float,
    key: Optional[str],
    singer_id: Optional[str],
    engine_style: Optional[str],
    duration_sec: float,
    out_path: Path,
    src_audio_path: Optional[Path],
    cover_strength: float,
    seed: Optional[int],
    full_lyrics: bool,
    progress: ProgressCb = None,
) -> GenerateResult:
    if progress:
        progress(20, "連線 AI 唱歌引擎")

    lyric_text = format_lyrics(lyrics, full=full_lyrics)
    if not lyric_text:
        raise RuntimeError("歌詞為空，無法產生 AI 人聲版")

    prompt = build_prompt(
        singer_id=singer_id,
        engine_style=engine_style,
        title=lyrics.get("title"),
        extend=bool(full_lyrics),
    )
    duration_sec = max(20.0, min(120.0, float(duration_sec)))
    bpm_i = int(max(30, min(300, round(float(bpm))))) if bpm else None
    use_cover = bool(src_audio_path and Path(src_audio_path).is_file())

    fields: Dict[str, Any] = {
        "prompt": prompt,
        "caption": prompt,
        "lyrics": lyric_text,
        "vocal_language": "zh",
        "thinking": "false",  # cover 本來就不用 LM；multipart 用字串
        "instrumental": "false",
        "audio_format": "mp3" if str(out_path).lower().endswith(".mp3") else "wav",
        "model": ACESTEP_MODEL,
        "audio_duration": str(duration_sec),
        "time_signature": "4",
        "batch_size": "1",
        "inference_steps": "8",
    }
    if use_cover:
        fields["task_type"] = "cover"
        fields["audio_cover_strength"] = str(max(0.05, min(1.0, float(cover_strength))))
    else:
        fields["task_type"] = "text2music"
        fields["thinking"] = "true" if ACESTEP_THINKING else "false"
    if bpm_i:
        fields["bpm"] = str(bpm_i)
    key_scale = format_key_scale(key)
    if key_scale:
        fields["key_scale"] = key_scale
    if seed is not None and int(seed) >= 0:
        fields["use_random_seed"] = "false"
        fields["seed"] = str(int(seed))
    if ACESTEP_API_KEY:
        fields["ai_token"] = ACESTEP_API_KEY

    files = None
    if use_cover:
        src = Path(src_audio_path)
        mime = "audio/mpeg" if src.suffix.lower() == ".mp3" else "audio/wav"
        files = {
            "src_audio": (src.name, src.read_bytes(), mime),
        }

    if progress:
        progress(35, "AI 正在依旋律唱歌" if use_cover else "AI 正在作曲唱歌")

    r = requests.post(
        f"{ACESTEP_URL}/release_task",
        headers=_headers(json_body=False),
        data=fields,
        files=files,
        timeout=120,
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

    result = _poll_and_download(
        task_id, out_path, progress=progress, duration_sec=duration_sec
    )
    if use_cover:
        result.engine = "acestep_cover"
    else:
        result.engine = "acestep"
    return result


def _generate_via_remote(
    *,
    lyrics: dict,
    bpm: float,
    key: Optional[str],
    singer_id: Optional[str],
    engine_style: Optional[str],
    duration_sec: float,
    out_path: Path,
    src_audio_path: Optional[Path],
    cover_strength: float,
    seed: Optional[int],
    full_lyrics: bool,
    progress: ProgressCb = None,
) -> GenerateResult:
    if progress:
        progress(20, "連線本機 AI 唱歌引擎")

    last_err = None
    for base in ACESTEP_REMOTE_URLS:
        try:
            if progress:
                progress(35, "AI 正在依旋律唱歌")
            data = {
                "lyrics_json": json.dumps(lyrics, ensure_ascii=False),
                "bpm": str(bpm),
                "key": key or "",
                "singer_id": singer_id or "",
                "engine_style": engine_style or "",
                "duration_sec": str(duration_sec),
                "cover_strength": str(cover_strength),
                "full_lyrics": "1" if full_lyrics else "0",
            }
            if seed is not None and int(seed) >= 0:
                data["seed"] = str(int(seed))
            files = None
            if src_audio_path and Path(src_audio_path).is_file():
                src = Path(src_audio_path)
                mime = "audio/mpeg" if src.suffix.lower() == ".mp3" else "audio/wav"
                files = {"src_audio": (src.name, src.read_bytes(), mime)}
            r = requests.post(
                f"{base}/acestep/generate",
                headers=_headers(json_body=False),
                data=data,
                files=files,
                timeout=ACESTEP_TIMEOUT_SEC + 30,
            )
            if r.status_code >= 400:
                last_err = f"HTTP {r.status_code}: {r.text[:240]}"
                continue
            ctype = (r.headers.get("content-type") or "").lower()
            if "json" in ctype and "audio" not in ctype:
                last_err = f"unexpected json: {r.text[:240]}"
                continue
            if len(r.content) < 5000:
                last_err = "remote audio too small"
                continue
            if progress:
                progress(90, "輸出成品")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(r.content)
            seed_hdr = r.headers.get("X-ACE-Seed") or r.headers.get("x-ace-seed")
            return GenerateResult(
                path=out_path,
                seed=seed_hdr,
                duration_sec=duration_sec,
                engine="acestep_cover" if files else "acestep",
            )
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
    duration_sec: float = TEASER_DURATION_SEC,
    out_path: Path,
    src_audio_path: Optional[Path] = None,
    cover_strength: float = ACESTEP_COVER_STRENGTH,
    seed: Optional[int] = None,
    full_lyrics: bool = False,
    progress: ProgressCb = None,
    force_local: bool = False,
) -> GenerateResult:
    """
    產生整曲並寫入 out_path。
    若提供 src_audio_path，走 cover（鎖預覽旋律）。
    """
    kwargs = dict(
        lyrics=lyrics,
        bpm=bpm,
        key=key,
        singer_id=singer_id,
        engine_style=engine_style,
        duration_sec=duration_sec,
        out_path=out_path,
        src_audio_path=Path(src_audio_path) if src_audio_path else None,
        cover_strength=cover_strength,
        seed=seed,
        full_lyrics=full_lyrics,
        progress=progress,
    )
    if force_local or local_available():
        return _generate_via_local_api(**kwargs)
    if ACESTEP_REMOTE_URLS:
        return _generate_via_remote(**kwargs)
    raise RuntimeError("ACE-Step 未啟動（本機 :8001 無回應）")


def generate_to_tempfile(**kwargs) -> GenerateResult:
    """給 HTTP 代理用：寫到暫存 mp3。"""
    fd, path = tempfile.mkstemp(prefix="acestep_", suffix=".mp3")
    os.close(fd)
    out = Path(path)
    try:
        return generate_to_file(out_path=out, force_local=True, **kwargs)
    except Exception:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
        raise
