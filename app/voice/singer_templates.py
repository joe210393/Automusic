"""
AI 歌手模板：男／女聲各三種。

重要：DiffSinger Opencpop 是女聲模型，代唱必須維持其自然音域（約 MIDI 64）。
男／女差異用「合成後變調」與輕量音色處理，不要把音符硬折到過低音域
（那會讓模型唱出鬼叫聲）。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import soundfile as sf

REFS_DIR = Path(__file__).resolve().parent / "singer_refs"

# DiffSinger Opencpop 舒適音域中心（女聲）
DIFFSINGER_NATIVE_MIDI = 64.0

SINGER_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "female_bright": {
        "id": "female_bright",
        "gender": "female",
        "label": "明亮清透",
        "blurb": "偏亮、像海邊陽光",
        "pitch_shift": 1.5,
        "gain": 1.04,
        "high_shelf": 1.12,
    },
    "female_warm": {
        "id": "female_warm",
        "gender": "female",
        "label": "溫暖柔光",
        "blurb": "溫柔中性，適合旅行回憶",
        "pitch_shift": 0.0,
        "gain": 1.0,
        "high_shelf": 1.0,
    },
    "female_soft": {
        "id": "female_soft",
        "gender": "female",
        "label": "柔和細語",
        "blurb": "更柔一點、輕一點",
        "pitch_shift": -1.0,
        "gain": 0.95,
        "high_shelf": 0.88,
    },
    "male_deep": {
        "id": "male_deep",
        "gender": "male",
        "label": "低沉厚實",
        "blurb": "沉穩、有重量",
        "pitch_shift": -11.0,
        "gain": 1.06,
        "high_shelf": 0.9,
    },
    "male_warm": {
        "id": "male_warm",
        "gender": "male",
        "label": "溫暖沉穩",
        "blurb": "中低、有溫度",
        "pitch_shift": -9.0,
        "gain": 1.02,
        "high_shelf": 0.95,
    },
    "male_clear": {
        "id": "male_clear",
        "gender": "male",
        "label": "清朗有力",
        "blurb": "清楚、有存在感",
        "pitch_shift": -7.0,
        "gain": 1.04,
        "high_shelf": 1.05,
    },
}


def list_templates(gender: Optional[str] = None) -> List[Dict[str, Any]]:
    items = list(SINGER_TEMPLATES.values())
    if gender in ("female", "male"):
        items = [t for t in items if t["gender"] == gender]
    return [
        {
            "id": t["id"],
            "gender": t["gender"],
            "label": t["label"],
            "blurb": t["blurb"],
            "has_ref": ref_wav_path(t["id"]).exists(),
        }
        for t in items
    ]


def get_template(singer_id: Optional[str]) -> Dict[str, Any]:
    if singer_id and singer_id in SINGER_TEMPLATES:
        return SINGER_TEMPLATES[singer_id]
    return SINGER_TEMPLATES["female_warm"]


def is_valid_singer_id(singer_id: Optional[str]) -> bool:
    return bool(singer_id) and singer_id in SINGER_TEMPLATES


def ref_wav_path(singer_id: str) -> Path:
    return REFS_DIR / f"{singer_id}.wav"


def _pitch_shift_audio(x: np.ndarray, fs: int, semitones: float) -> np.ndarray:
    """用 ffmpeg 變調並維持時長（asetrate + atempo）。"""
    if abs(semitones) < 0.05 or x.size < 32:
        return x
    rate = 2.0 ** (semitones / 12.0)
    # 變速補償：asetrate 改變音高也改變速度，atempo 拉回原長
    tempo = rate
    filters = [f"asetrate={fs * rate:.4f}", f"aresample={fs}"]
    # atempo 只接受 0.5–2.0
    r = tempo
    while r > 2.0:
        filters.append("atempo=2.0")
        r /= 2.0
    while r < 0.5:
        filters.append("atempo=0.5")
        r /= 0.5
    filters.append(f"atempo={r:.6f}")
    af = ",".join(filters)

    in_path = tempfile.mktemp(prefix="tpl_in_", suffix=".wav")
    out_path = tempfile.mktemp(prefix="tpl_out_", suffix=".wav")
    try:
        sf.write(in_path, x.astype(np.float32), fs)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", in_path, "-af", af, out_path],
            check=True,
            capture_output=True,
            timeout=180,
        )
        y, out_fs = sf.read(out_path, dtype="float64")
        if y.ndim > 1:
            y = y.mean(axis=1)
        if out_fs != fs:
            n = int(len(y) * fs / out_fs)
            y = np.interp(np.linspace(0, len(y) - 1, n), np.arange(len(y)), y)
        # 對齊長度
        if len(y) < len(x):
            y = np.pad(y, (0, len(x) - len(y)))
        return y[: len(x)]
    except Exception as e:
        print(f"[singer-templates] pitch shift fail: {e}")
        return x
    finally:
        for p in (in_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _high_shelf(x: np.ndarray, amount: float) -> np.ndarray:
    """amount>1 提亮，amount<1 變柔；用簡易差分近似。"""
    if abs(amount - 1.0) < 0.02 or x.size < 4:
        return x
    # high = x - smooth(x)
    k = 5
    kernel = np.ones(k, dtype=np.float64) / k
    smooth = np.convolve(x, kernel, mode="same")
    high = x - smooth
    # amount=1.12 → 多加一點高頻；0.88 → 減少高頻
    return smooth + high * float(amount)


def apply_arrangement_color(audio: np.ndarray, singer_id: Optional[str]) -> np.ndarray:
    """
    對完整編曲（可 stereo）套輕量音色差異，讓六種模板可區分，
    但不做人聲合成（避免 DiffSinger 鬼聲）。
    """
    tpl = get_template(singer_id)
    x = np.asarray(audio, dtype=np.float64)
    if x.size == 0:
        return x
    mono = False
    if x.ndim == 1:
        mono = True
        x = x[:, None]
    out = []
    shelf = float(tpl.get("high_shelf") or 1.0)
    gain = float(tpl.get("gain") or 1.0)
    # 男聲模板：略降「明亮感」、略加厚度；女聲相反
    if tpl.get("gender") == "male":
        shelf = min(shelf, 0.96)
        gain *= 1.03
    for ch in range(x.shape[1]):
        c = _high_shelf(x[:, ch], shelf) * gain
        out.append(c)
    y = np.stack(out, axis=1)
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > 0.99:
        y *= 0.99 / peak
    if mono:
        return y[:, 0]
    return y


def apply_template_color(vocal: np.ndarray, singer_id: Optional[str], fs: int = 44100) -> np.ndarray:
    """合成後套用模板：變調（男聲）＋輕量音色，避免破壞 DiffSinger 音質。"""
    tpl = get_template(singer_id)
    x = np.asarray(vocal, dtype=np.float64).copy()
    if x.size == 0:
        return x

    shift = float(tpl.get("pitch_shift") or 0.0)
    if abs(shift) >= 0.05:
        x = _pitch_shift_audio(x, fs, shift)

    x = _high_shelf(x, float(tpl.get("high_shelf") or 1.0))
    x *= float(tpl.get("gain") or 1.0)

    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak > 0.95:
        x *= 0.95 / peak
    return x.astype(np.float64)
