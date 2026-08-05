"""
神經歌聲轉換（Seed-VC）：把 WORLD 聲碼器合成的「音高正確但機械」的人聲底稿，
用零樣本聲音轉換模型重新生成成自然的人聲（音色來自使用者的聲紋錄音，不需訓練）。

Seed-VC 安裝在獨立目錄（預設 ~/seed-vc，自帶 venv），用 subprocess 呼叫，
跟主應用的依賴完全隔離。沒安裝的環境（例如 Zeabur 容器）自動退回聲碼器輸出，
或透過 VC_REMOTE_URLS 把轉換委託給有裝 Seed-VC 的機器（例如經 ngrok 的本地電腦）。
"""
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

SEED_VC_DIR = Path(os.getenv("SEED_VC_DIR", str(Path.home() / "seed-vc")))

# 雲端部署時把轉換委託給本地電腦（逗號分隔多個網址，依序嘗試）。
# 預設走 ngrok 固定網域（指向本地 8080 的 FastAPI，它有 /vc/convert）。
_default_vc_urls = "https://tactually-venerable-inez.ngrok-free.dev"
VC_REMOTE_URLS = [
    u.strip() for u in os.getenv("VC_REMOTE_URLS", _default_vc_urls).split(",") if u.strip()
]


def is_available() -> bool:
    """本機是否裝好 Seed-VC（原始碼＋自己的 venv）。"""
    return (
        (SEED_VC_DIR / "inference.py").exists()
        and (SEED_VC_DIR / ".venv" / "bin" / "python").exists()
    )


def build_reference_wav(voiceprint_dir: Path, manifest: dict, max_seconds: float = 20.0) -> Optional[str]:
    """把幾句聲紋錄音串成一個音色參考檔（Seed-VC 建議 1~30 秒）。"""
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
    ref_path = tempfile.mktemp(prefix="vc_ref_", suffix=".wav")
    sf.write(ref_path, ref, fs)
    return ref_path


def convert_voice_local(source_wav: str, reference_wav: str, diffusion_steps: int = 30,
                        timeout: int = 1200) -> Optional[str]:
    """在本機跑 Seed-VC 歌聲轉換（f0-condition 保留旋律音高）。回傳輸出 wav 路徑。"""
    if not is_available():
        return None
    out_dir = tempfile.mkdtemp(prefix="seedvc_out_")
    env = {**os.environ, "PYTORCH_ENABLE_MPS_FALLBACK": "1"}
    cmd = [
        str(SEED_VC_DIR / ".venv" / "bin" / "python"), "inference.py",
        "--source", source_wav,
        "--target", reference_wav,
        "--output", out_dir,
        "--diffusion-steps", str(diffusion_steps),
        "--length-adjust", "1.0",
        "--inference-cfg-rate", "0.7",
        "--f0-condition", "True",       # 歌聲轉換：跟著底稿的旋律音高
        "--auto-f0-adjust", "False",
        "--semi-tone-shift", "0",
    ]
    try:
        subprocess.run(
            cmd, cwd=str(SEED_VC_DIR), env=env,
            check=True, capture_output=True, timeout=timeout,
        )
    except subprocess.CalledProcessError as e:
        print(f"[neural-vc] Seed-VC 執行失敗：{e.stderr.decode(errors='ignore')[-500:]}")
        return None
    except Exception as e:
        print(f"[neural-vc] Seed-VC 執行失敗：{e}")
        return None
    wavs = sorted(Path(out_dir).glob("*.wav"))
    return str(wavs[0]) if wavs else None


def convert_voice_remote(source_wav: str, reference_wav: str, timeout: int = 1200) -> Optional[str]:
    """把轉換委託給遠端機器的 /vc/convert（例如 Zeabur → 本地 Mac）。"""
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
                    files={"source": ("source.wav", sf_, "audio/wav"),
                           "reference": ("reference.wav", rf_, "audio/wav")},
                    timeout=(8, timeout),
                )
            if resp.status_code != 200 or len(resp.content) < 1000:
                raise RuntimeError(f"HTTP {resp.status_code}")
            out_path = tempfile.mktemp(prefix="vc_remote_", suffix=".wav")
            with open(out_path, "wb") as f:
                f.write(resp.content)
            return out_path
        except Exception as e:
            print(f"[neural-vc] 遠端轉換失敗（{url}）：{e}")
            continue
    return None


def convert_voice(source_wav: str, reference_wav: str) -> Optional[str]:
    """先試本機 Seed-VC，再試遠端委託；都不行回傳 None（改用聲碼器底稿）。"""
    out = convert_voice_local(source_wav, reference_wav)
    if out:
        return out
    return convert_voice_remote(source_wav, reference_wav)
