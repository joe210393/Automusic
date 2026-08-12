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
ACESTEP_COVER_STRENGTH = float(os.getenv("ACESTEP_COVER_STRENGTH", "0.68"))
# >0 讓 cover 有空間長出人聲；太高會黏死伴奏、無人聲
ACESTEP_COVER_NOISE = float(os.getenv("ACESTEP_COVER_NOISE", "0.28"))
ACESTEP_GUIDANCE = float(os.getenv("ACESTEP_GUIDANCE", "8.5"))
ACESTEP_POLL_INTERVAL = float(os.getenv("ACESTEP_POLL_INTERVAL", "2.0"))
ACESTEP_TIMEOUT_SEC = float(os.getenv("ACESTEP_TIMEOUT_SEC", "900"))

TEASER_DURATION_SEC = float(os.getenv("ACESTEP_TEASER_DURATION", "60"))
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
        "cover the source accompaniment as Song A",
        "preserve the original melody contour rhythm and chords of Song A",
        "same travel melody — only slight variation allowed, not a new song",
        "add a clearly audible lead singer in the foreground singing Chinese lyrics",
        "vocals must be present and prominent, not buried, not instrumental-only",
        "do not invent a new unrelated melody",
    ]
    if extend:
        bits.extend([
            "extend the same Song A into a longer verse-chorus form",
            "keep the identical travel melody while lengthening structure",
            "about two minutes of the same song, not a different composition",
        ])
    if engine_style:
        bits.append(str(engine_style))
    if title:
        bits.append(f"song about: {title}")
    return ", ".join(bits)


def _ffprobe_duration(path: Path) -> Optional[float]:
    try:
        import subprocess
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return float(out)
    except Exception:
        return None


def fit_cover_source(src_audio_path: Path, target_sec: float, *, out_dir: Optional[Path] = None) -> Path:
    """
    ACE-Step cover 的輸出長度 = src_audio 長度。
    把歌曲 A（伴奏）裁成試聽長、或循環延長成完整版長，再送去 cover。
    """
    import subprocess

    src = Path(src_audio_path)
    if not src.is_file():
        raise RuntimeError(f"找不到歌曲 A 伴奏：{src}")
    target = max(20.0, min(120.0, float(target_sec)))
    work = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="ace_song_a_"))
    work.mkdir(parents=True, exist_ok=True)
    out = work / f"song_a_{int(round(target))}s.wav"

    src_dur = _ffprobe_duration(src) or 0.0
    fade_out = min(1.2, target * 0.04)
    fade_st = max(0.0, target - fade_out)
    af = f"afade=t=in:st=0:d=0.05,afade=t=out:st={fade_st:.2f}:d={fade_out:.2f}"

    if src_dur >= target - 0.25:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(src),
            "-t", f"{target:.2f}",
            "-ac", "2", "-ar", "48000",
            "-af", af,
            str(out),
        ]
    else:
        # 循環歌曲 A 到目標長度（同一旋律延長，不是另寫新歌）
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-stream_loop", "-1",
            "-i", str(src),
            "-t", f"{target:.2f}",
            "-ac", "2", "-ar", "48000",
            "-af", af,
            str(out),
        ]
    try:
        subprocess.check_call(cmd)
    except Exception as e:
        raise RuntimeError(f"無法準備歌曲 A cover 素材: {e}") from e
    if not out.is_file() or out.stat().st_size < 2000:
        raise RuntimeError("歌曲 A cover 素材異常過短")
    return out


def vocal_presence_score(path: Path) -> float:
    """粗估人聲存在感（中頻能量占比 0~1）。"""
    try:
        import subprocess
        import numpy as np
        import soundfile as sf

        wav = Path(tempfile.mktemp(suffix=".wav"))
        subprocess.check_call(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", "16000", str(wav)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        audio, sr = sf.read(wav)
        wav.unlink(missing_ok=True)
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        spec = np.abs(np.fft.rfft(audio))
        freqs = np.fft.rfftfreq(len(audio), 1 / sr)
        def band(lo, hi):
            m = (freqs >= lo) & (freqs < hi)
            return float(np.mean(spec[m] ** 2)) if m.any() else 0.0
        bass, mid, high = band(20, 250), band(250, 3400), band(3400, 8000)
        tot = bass + mid + high + 1e-12
        return mid / tot
    except Exception:
        return 0.0


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
    cover_noise: Optional[float] = None,
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
    noise = ACESTEP_COVER_NOISE if cover_noise is None else float(cover_noise)

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
        "inference_steps": "10",
        "guidance_scale": str(ACESTEP_GUIDANCE),
    }
    if use_cover:
        fields["task_type"] = "cover"
        fields["audio_cover_strength"] = str(max(0.05, min(1.0, float(cover_strength))))
        fields["cover_noise_strength"] = str(max(0.0, min(1.0, noise)))
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
        progress(35, "AI 正在依歌曲 A 唱歌" if use_cover else "AI 正在作曲唱歌")

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
    cover_noise: Optional[float] = None,
) -> GenerateResult:
    if progress:
        progress(20, "連線本機 AI 唱歌引擎")

    last_err = None
    for base in ACESTEP_REMOTE_URLS:
        try:
            if progress:
                progress(35, "AI 正在依歌曲 A 唱歌")
            data = {
                "lyrics_json": json.dumps(lyrics, ensure_ascii=False),
                "bpm": str(bpm),
                "key": key or "",
                "singer_id": singer_id or "",
                "engine_style": engine_style or "",
                "duration_sec": str(duration_sec),
                "cover_strength": str(cover_strength),
                "cover_noise": str(ACESTEP_COVER_NOISE if cover_noise is None else cover_noise),
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
    cover_noise: Optional[float] = None,
    fit_source_duration: bool = True,
) -> GenerateResult:
    """
    產生整曲並寫入 out_path。
    若提供 src_audio_path，走 cover（鎖歌曲 A 旋律）。
    cover 時會先把歌曲 A 裁切／循環成目標秒數（ACE-Step cover 長度=來源長度）。
    """
    fitted = None
    try:
        src = Path(src_audio_path) if src_audio_path else None
        if src and src.is_file() and fit_source_duration:
            if progress:
                progress(12, "準備歌曲 A 長度")
            fitted = fit_cover_source(src, duration_sec)
            src = fitted
        kwargs = dict(
            lyrics=lyrics,
            bpm=bpm,
            key=key,
            singer_id=singer_id,
            engine_style=engine_style,
            duration_sec=duration_sec,
            out_path=out_path,
            src_audio_path=src,
            cover_strength=cover_strength,
            seed=seed,
            full_lyrics=full_lyrics,
            progress=progress,
            cover_noise=cover_noise,
        )
        if force_local or local_available():
            return _generate_via_local_api(**kwargs)
        if ACESTEP_REMOTE_URLS:
            return _generate_via_remote(**kwargs)
        raise RuntimeError("ACE-Step 未啟動（本機 :8001 無回應）")
    finally:
        if fitted and fitted.exists():
            try:
                fitted.unlink(missing_ok=True)
                parent = fitted.parent
                if parent.name.startswith("ace_song_a_"):
                    parent.rmdir()
            except OSError:
                pass

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
