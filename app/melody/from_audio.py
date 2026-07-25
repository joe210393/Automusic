"""
從素材聲音生成旋律

核心理念：使用者收集的聲音是「靈感素材」，不是旋律本身。
系統從素材中萃取「元素與感覺」，生成一段帶有素材 DNA 的新旋律：

    元素（具體）：
    - 動機（motif）：素材開頭最突出的幾個音，會直接出現在旋律的第 1、3 小節
    - 音域：旋律落在素材本身的音域附近

    感覺（抽象）：
    - 明暗：素材偏大調色彩 → 大調五聲；偏小調色彩 → 小調五聲
    - 能量：素材的聲音密度決定 BPM 與節奏型的密度
    - 走向：素材整體音高上行/下行，影響旋律的行進傾向
"""

import random
from typing import Optional

from app.melody.generator import build_scale_pitches, SCALE_INTERVALS

NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

# 依能量分級的節奏型（單位：拍，總和 4）
RHYTHMS_LOW = [[2, 2], [1, 1, 2], [2, 1, 1], [1, 1, 1, 1]]
RHYTHMS_MID = [[1, 1, 1, 1], [1, 0.5, 0.5, 1, 1], [1.5, 0.5, 1, 1], [1, 1, 2]]
RHYTHMS_HIGH = [[0.5, 0.5, 1, 0.5, 0.5, 1], [1, 0.5, 0.5, 1, 1], [0.5, 0.5, 0.5, 0.5, 1, 1]]


def _extract_features(mat_notes: list) -> dict:
    """從素材音符萃取特徵。"""
    # 時值加權的音級直方圖
    pc_weight = [0.0] * 12
    for n in mat_notes:
        pc_weight[n["midi"] % 12] += max(0.0, n["end"] - n["start"])

    root_pc = max(range(12), key=lambda i: pc_weight[i])

    # 明暗：根音上方小三度 vs 大三度誰的份量重
    minor_w = pc_weight[(root_pc + 3) % 12]
    major_w = pc_weight[(root_pc + 4) % 12]
    is_minor = minor_w > major_w

    # 音域中心（中位數）
    pitches = sorted(n["midi"] for n in mat_notes)
    center = pitches[len(pitches) // 2] if pitches else 66
    center = max(55, min(80, center))

    # 能量：每秒音符數
    total_dur = max((n["end"] for n in mat_notes), default=1.0)
    density = len(mat_notes) / max(total_dur, 0.5)

    # 走向：後半段平均音高 vs 前半段
    contour = 0
    if len(mat_notes) >= 4:
        half = len(mat_notes) // 2
        first = sum(n["midi"] for n in mat_notes[:half]) / half
        second = sum(n["midi"] for n in mat_notes[half:]) / (len(mat_notes) - half)
        if second - first > 1.5:
            contour = 1
        elif first - second > 1.5:
            contour = -1

    # 動機：素材開頭最多 4 個音
    motif = [n["midi"] for n in mat_notes[:4]]

    return {
        "root_pc": root_pc,
        "is_minor": is_minor,
        "center": center,
        "density": density,
        "contour": contour,
        "motif": motif,
    }


def _snap_to_scale(pitch: int, scale_pitches: list) -> int:
    """把任意音高吸附到音階內最近的音。"""
    return min(scale_pitches, key=lambda s: abs(s - pitch))


def generate_melody_from_material(audio_path: str, seed: Optional[int] = None) -> dict:
    """
    分析素材聲音，生成帶有素材元素與感覺的 4 小節旋律。

    Returns:
        {
          "notes": [...], "bpm": float, "key": str,          # 可直接接 /render-music 流程
          "material": {...特徵摘要，給前端顯示...}
        }
    """
    from app.audio.extract_notes import extract_notes_from_audio

    data = extract_notes_from_audio(audio_path)
    mat_notes = data["notes"]
    if not mat_notes:
        raise ValueError("素材中偵測不到任何聲音事件")

    feat = _extract_features(mat_notes)
    rng = random.Random(seed)

    # --- 感覺 → 參數 ---
    scale_type = "minor_pentatonic" if feat["is_minor"] else "major_pentatonic"
    intervals = SCALE_INTERVALS[scale_type]

    if feat["density"] < 1.0:
        bpm, rhythms = 75.0, RHYTHMS_LOW
    elif feat["density"] < 2.0:
        bpm, rhythms = 90.0, RHYTHMS_MID
    elif feat["density"] < 3.5:
        bpm, rhythms = 105.0, RHYTHMS_MID
    else:
        bpm, rhythms = 120.0, RHYTHMS_HIGH

    # 音階範圍以素材音域為中心
    low = max(48, feat["center"] - 10)
    high = min(88, feat["center"] + 10)
    scale_pitches = build_scale_pitches(feat["root_pc"], intervals, low, high)
    while len(scale_pitches) < 5:
        low = max(36, low - 4)
        high = min(96, high + 4)
        scale_pitches = build_scale_pitches(feat["root_pc"], intervals, low, high)

    tonic_pitches = [p for p in scale_pitches if p % 12 == feat["root_pc"]]

    # 動機吸附到音階
    motif_scale = [_snap_to_scale(p, scale_pitches) for p in feat["motif"]]
    if not motif_scale:
        motif_scale = [tonic_pitches[len(tonic_pitches) // 2] if tonic_pitches else scale_pitches[len(scale_pitches) // 2]]

    # 走向 → 隨機漫步的偏移權重
    if feat["contour"] > 0:
        step_weights = [1, 3, 10, 16, 6, 26, 20, 8, 4]   # 偏上行
    elif feat["contour"] < 0:
        step_weights = [4, 8, 20, 26, 6, 16, 10, 3, 1]   # 偏下行
    else:
        step_weights = [2, 4, 14, 22, 8, 22, 14, 4, 2]
    steps = [-4, -3, -2, -1, 0, 1, 2, 3, 4]

    # --- 生成 4 小節：動機 | 發展 | 動機 | 收尾 ---
    beat_sec = 60.0 / bpm
    notes = []
    current_time = 0.0
    NUM_BARS = 4

    idx = scale_pitches.index(motif_scale[0])

    for bar in range(NUM_BARS):
        is_motif_bar = bar in (0, 2)
        is_last_bar = bar == NUM_BARS - 1

        if is_last_bar:
            pattern = rng.choice([[2, 2], [1, 1, 2], [2, 1, 1]])
        else:
            pattern = rng.choice(rhythms)

        for i, dur_beats in enumerate(pattern):
            is_final_note = is_last_bar and i == len(pattern) - 1

            if is_motif_bar and i < len(motif_scale):
                # 動機音：素材的元素直接進旋律
                idx = scale_pitches.index(motif_scale[i % len(motif_scale)])
            elif is_final_note:
                # 收尾回主音
                if tonic_pitches:
                    idx = scale_pitches.index(
                        min(tonic_pitches, key=lambda p: abs(p - scale_pitches[idx]))
                    )
            else:
                step = rng.choices(steps, weights=step_weights)[0]
                idx = max(0, min(len(scale_pitches) - 1, idx + step))

            dur_sec = dur_beats * beat_sec
            note_len = dur_sec * (1.0 if is_final_note else 0.9)
            beat_pos = sum(pattern[:i])
            on_downbeat = abs(beat_pos - round(beat_pos)) < 1e-6 and int(round(beat_pos)) % 2 == 0
            velocity = rng.randint(88, 100) if on_downbeat else rng.randint(76, 90)

            notes.append({
                "start": round(current_time, 4),
                "end": round(current_time + note_len, 4),
                "midi": scale_pitches[idx],
                "velocity": velocity,
            })
            current_time += dur_sec

    # 和弦引擎只支援大調：小調感覺時用關係大調當調性（如 A 小調 → C 大調和弦）
    key_pc = (feat["root_pc"] + 3) % 12 if feat["is_minor"] else feat["root_pc"]
    key_name = NOTE_NAMES[key_pc]

    return {
        "notes": notes,
        "bpm": bpm,
        "key": key_name,
        "material": {
            "root": NOTE_NAMES[feat["root_pc"]],
            "mood": "小調（沉靜/憂鬱）" if feat["is_minor"] else "大調（明亮/開朗）",
            "energy": round(feat["density"], 1),
            "bpm": bpm,
            "contour": {1: "上行", -1: "下行", 0: "平穩"}[feat["contour"]],
            "num_material_notes": len(mat_notes),
        },
    }
