"""
輕量母帶鏈：ACE lossless → EQ／壓縮／響度 → MP3。

不做激烈染色；目標是更清楚、更穩、接近 -14～-12 LUFS。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def find_ffmpeg() -> Optional[str]:
    for c in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if os.path.exists(c):
            return c
    return None


def _af_chain() -> str:
    # high-pass 去泥 → 人聲區微亮 → 輕壓 → loudnorm
    return (
        "highpass=f=70,"
        "equalizer=f=3500:t=q:w=1.2:g=1.8,"
        "equalizer=f=200:t=q:w=1:g=-1.2,"
        "acompressor=threshold=-18dB:ratio=2.2:attack=15:release=180:makeup=2,"
        "loudnorm=I=-13:TP=-1.5:LRA=11"
    )


def master_to_mp3(
    src_path: str,
    *,
    out_mp3: Optional[str] = None,
    bitrate: str = "192k",
) -> Optional[str]:
    """
    把 WAV/FLAC（或已是音訊）做成母帶 MP3。
    成功回傳 mp3 路徑；失敗回傳 None（呼叫端可退回 compress_to_mp3）。
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    src = Path(src_path)
    if not src.exists():
        return None
    dest = Path(out_mp3) if out_mp3 else src.with_suffix(".mp3")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".mastering.tmp.mp3")
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(src),
                "-af",
                _af_chain(),
                "-codec:a",
                "libmp3lame",
                "-b:a",
                bitrate,
                str(tmp),
            ],
            check=True,
            capture_output=True,
            timeout=180,
        )
    except Exception as e:
        print(f"[mastering] failed: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    if not tmp.exists() or tmp.stat().st_size < 1000:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    shutil.move(str(tmp), str(dest))
    return str(dest)
