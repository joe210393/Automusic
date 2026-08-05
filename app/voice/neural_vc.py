"""
神經歌聲轉換（Seed-VC）：
把「音高／節奏正確、音色仍像說話」的人聲底稿，用零樣本模型轉成更自然的歌聲。
音色參考來自步驟 5 的聲紋錄音，不需訓練。

長音檔會切成約 12 秒一段分開轉換再接回（長檔一次丟進去品質很差）。
"""
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

SEED_VC_DIR = Path(os.getenv("SEED_VC_DIR", str(Path.home() / "seed-vc")))

_default_vc_urls = "https://tactually-venerable-inez.ngrok-free.dev"
VC_REMOTE_URLS = [
    u.strip() for u in os.getenv("VC_REMOTE_URLS", _default_vc_urls).split(",") if u.strip()
]

CHUNK_SECONDS = 12.0
CHUNK_OVERLAP = 0.25  # 秒，交叉淡入淡出


def is_available() -> bool:
    return (
        (SEED_VC_DIR / "inference.py").exists()
        and (SEED_VC_DIR / ".venv" / "bin" / "python").exists()
    )


def build_reference_wav(voiceprint_dir: Path, manifest: dict, max_seconds: float = 20.0) -> Optional[str]:
    from app.voice.sing import load_mono, trim_silence

    fs = 44100
    parts = []
    total = 0.0
    lines = sorted(
        manifest.get("lines", []),
        key=lambda l: (0 if l.get("section") == "verse" else 1, l.get("index", 0)),
    )
    for line in lines:
        path = voiceprint_dir / line.get("filename", "")
        if not path.exists():
            continue
        try:
            x = trim_silence(load_mono(str(path), fs), fs)
        except Exception:
            continue
        if len(x) < fs // 10:
            continue
        parts.append(x)
        total += len(x) / fs
        if total >= max_seconds:
            break
    if not parts:
        return None
    ref = np.concatenate(parts)
    # 響度正規化，避免參考音太小聲讓轉換飄掉
    peak = float(np.max(np.abs(ref)))
    if peak > 1e-6:
        ref = ref * (0.8 / peak)
    ref_path = tempfile.mktemp(prefix="vc_ref_", suffix=".wav")
    sf.write(ref_path, ref, fs)
    return ref_path


def _run_seedvc_once(source_wav: str, reference_wav: str, out_dir: str,
                     diffusion_steps: int, timeout: int) -> Optional[str]:
    env = {
        **os.environ,
        "PYTORCH_ENABLE_MPS_FALLBACK": "1",
        "HOME": os.environ.get("HOME") or str(Path.home()),
    }
    cmd = [
        str(SEED_VC_DIR / ".venv" / "bin" / "python"), "inference.py",
        "--source", source_wav,
        "--target", reference_wav,
        "--output", out_dir,
        "--diffusion-steps", str(diffusion_steps),
        "--length-adjust", "1.0",
        "--inference-cfg-rate", "0.7",
        "--f0-condition", "True",
        "--auto-f0-adjust", "False",
        "--semi-tone-shift", "0",
    ]
    try:
        subprocess.run(
            cmd, cwd=str(SEED_VC_DIR), env=env,
            check=True, capture_output=True, timeout=timeout,
        )
    except subprocess.CalledProcessError as e:
        print(f"[neural-vc] Seed-VC 失敗：{e.stderr.decode(errors='ignore')[-800:]}")
        return None
    except Exception as e:
        print(f"[neural-vc] Seed-VC 失敗：{e}")
        return None
    wavs = sorted(Path(out_dir).glob("*.wav"))
    return str(wavs[0]) if wavs else None


def convert_voice_local(source_wav: str, reference_wav: str, diffusion_steps: int = 50,
                        timeout: int = 1800) -> Optional[str]:
    """本機 Seed-VC；長音檔自動切片轉換。"""
    if not is_available():
        return None

    x, fs = sf.read(source_wav, dtype="float64")
    if x.ndim > 1:
        x = x.mean(axis=1)
    duration = len(x) / fs

    # 短於一個 chunk：直接轉
    if duration <= CHUNK_SECONDS + 1.0:
        out_dir = tempfile.mkdtemp(prefix="seedvc_out_")
        return _run_seedvc_once(source_wav, reference_wav, out_dir, diffusion_steps, timeout)

    # 長音檔：只轉「有聲音」的段落，靜音直接保留（省時間也比較穩）
    print(f"[neural-vc] 音檔 {duration:.1f}s，切成約 {CHUNK_SECONDS:.0f}s 一段轉換")
    chunk_n = int(CHUNK_SECONDS * fs)
    hop = int((CHUNK_SECONDS - CHUNK_OVERLAP) * fs)
    out = np.zeros_like(x)
    weight = np.zeros_like(x)
    fade = int(CHUNK_OVERLAP * fs)
    window = np.ones(chunk_n)
    if fade > 0 and fade * 2 < chunk_n:
        window[:fade] = np.linspace(0, 1, fade)
        window[-fade:] = np.linspace(1, 0, fade)

    pos = 0
    idx = 0
    while pos < len(x):
        end = min(pos + chunk_n, len(x))
        seg = x[pos:end]
        # 幾乎靜音的段落跳過轉換
        if float(np.sqrt(np.mean(seg ** 2))) < 1e-4:
            out[pos:end] += seg
            weight[pos:end] += 1.0
            pos += hop
            idx += 1
            continue

        seg_path = tempfile.mktemp(prefix=f"vc_chunk_{idx}_", suffix=".wav")
        sf.write(seg_path, seg, fs)
        out_dir = tempfile.mkdtemp(prefix=f"seedvc_chunk_{idx}_")
        converted = _run_seedvc_once(
            seg_path, reference_wav, out_dir, diffusion_steps,
            timeout=max(180, int(timeout * (end - pos) / len(x) * 2)),
        )
        if converted:
            y, yfs = sf.read(converted, dtype="float64")
            if y.ndim > 1:
                y = y.mean(axis=1)
            if yfs != fs:
                n = int(len(y) * fs / yfs)
                y = np.interp(np.linspace(0, len(y) - 1, n), np.arange(len(y)), y)
            if len(y) < len(seg):
                y = np.pad(y, (0, len(seg) - len(y)))
            y = y[: len(seg)]
            w = window[: len(seg)]
            out[pos:end] += y * w
            weight[pos:end] += w
        else:
            # 這段失敗就保留底稿，不要整首報廢
            out[pos:end] += seg
            weight[pos:end] += 1.0
        pos += hop
        idx += 1

    weight = np.maximum(weight, 1e-6)
    out = out / weight
    out_path = tempfile.mktemp(prefix="vc_stitched_", suffix=".wav")
    sf.write(out_path, out, fs)
    return out_path


def convert_voice_remote(source_wav: str, reference_wav: str, timeout: int = 1800) -> Optional[str]:
    if not VC_REMOTE_URLS:
        return None
    import requests

    for base in VC_REMOTE_URLS:
        url = base.rstrip("/") + "/vc/convert"
        try:
            with open(source_wav, "rb") as sf_, open(reference_wav, "rb") as rf_:
                resp = requests.post(
                    url,
                    headers={"ngrok-skip-browser-warning": "1"},
                    files={
                        "source": ("source.wav", sf_, "audio/wav"),
                        "reference": ("reference.wav", rf_, "audio/wav"),
                    },
                    timeout=(8, timeout),
                )
            if resp.status_code != 200 or len(resp.content) < 1000:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            out_path = tempfile.mktemp(prefix="vc_remote_", suffix=".wav")
            with open(out_path, "wb") as f:
                f.write(resp.content)
            return out_path
        except Exception as e:
            print(f"[neural-vc] 遠端轉換失敗（{url}）：{e}")
            continue
    return None


def convert_voice(source_wav: str, reference_wav: str) -> Optional[str]:
    """本機優先；本機沒裝 Seed-VC 才委託遠端。"""
    if is_available():
        out = convert_voice_local(source_wav, reference_wav)
        if out:
            return out
    return convert_voice_remote(source_wav, reference_wav)
