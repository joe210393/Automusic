"""
AI 歌手模板：男／女聲各三種。

實際音色差異：
1. DiffSinger 代唱時依 speaker_midi 折疊音域
2. 若 app/voice/singer_refs/{id}.wav 存在且 Seed-VC 可用，再換成該參考音色
3. 否則套用輕量等化／響度輪廓，讓六種仍可聽出差別
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

REFS_DIR = Path(__file__).resolve().parent / "singer_refs"

SINGER_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "female_bright": {
        "id": "female_bright",
        "gender": "female",
        "label": "女聲・明亮",
        "blurb": "清亮一點，像海邊陽光",
        "speaker_midi": 67.0,
        "gain": 1.05,
        "softness": 0.0,
    },
    "female_warm": {
        "id": "female_warm",
        "gender": "female",
        "label": "女聲・溫暖",
        "blurb": "溫柔中音，適合旅行回憶",
        "speaker_midi": 64.0,
        "gain": 1.0,
        "softness": 0.15,
    },
    "female_soft": {
        "id": "female_soft",
        "gender": "female",
        "label": "女聲・柔和",
        "blurb": "柔一點、輕一點",
        "speaker_midi": 61.0,
        "gain": 0.92,
        "softness": 0.35,
    },
    "male_deep": {
        "id": "male_deep",
        "gender": "male",
        "label": "男聲・低沉",
        "blurb": "沉穩低音",
        "speaker_midi": 48.0,
        "gain": 1.08,
        "softness": 0.1,
    },
    "male_warm": {
        "id": "male_warm",
        "gender": "male",
        "label": "男聲・溫暖",
        "blurb": "中低男聲，有溫度",
        "speaker_midi": 52.0,
        "gain": 1.0,
        "softness": 0.2,
    },
    "male_clear": {
        "id": "male_clear",
        "gender": "male",
        "label": "男聲・清朗",
        "blurb": "偏高男聲，較清楚",
        "speaker_midi": 56.0,
        "gain": 1.02,
        "softness": 0.05,
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


def apply_template_color(vocal: np.ndarray, singer_id: Optional[str], fs: int = 44100) -> np.ndarray:
    """無 Seed-VC 參考檔時，用增益與輕柔化做出可感知差異。"""
    tpl = get_template(singer_id)
    x = np.asarray(vocal, dtype=np.float64).copy()
    if x.size == 0:
        return x
    softness = float(tpl.get("softness") or 0.0)
    if softness > 0 and len(x) > 8:
        # 簡易移動平均＝略柔一點的高頻
        k = max(3, int(fs * 0.0004 * (1.0 + softness * 4)))
        if k % 2 == 0:
            k += 1
        kernel = np.ones(k, dtype=np.float64) / k
        smooth = np.convolve(x, kernel, mode="same")
        x = (1.0 - softness) * x + softness * smooth
    gain = float(tpl.get("gain") or 1.0)
    x *= gain
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak > 0.95:
        x *= 0.95 / peak
    return x.astype(np.float64)
