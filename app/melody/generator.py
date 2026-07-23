"""
地端旋律生成器

輸入音階（內建音階或自訂音名），完全在本機用演算法生成旋律：
- 音高：音階內的隨機漫步（以級進為主、偶爾跳進），樂句結尾傾向落在穩定音
- 節奏：從常見節奏型中挑選，每小節一組
- 收尾：最後一個音強制回到主音並拉長，聽起來有結束感

不需要任何外部 API 或模型。
"""

import random
from typing import List, Optional

# 音名 -> pitch class（支援升降記號）
NOTE_NAME_TO_PC = {
    "C": 0, "C#": 1, "DB": 1,
    "D": 2, "D#": 3, "EB": 3,
    "E": 4, "FB": 4, "E#": 5,
    "F": 5, "F#": 6, "GB": 6,
    "G": 7, "G#": 8, "AB": 8,
    "A": 9, "A#": 10, "BB": 10,
    "B": 11, "CB": 11,
}

# 內建音階：相對根音的半音間隔
SCALE_INTERVALS = {
    "major": [0, 2, 4, 5, 7, 9, 11],            # 大調
    "minor": [0, 2, 3, 5, 7, 8, 10],            # 自然小調
    "major_pentatonic": [0, 2, 4, 7, 9],        # 大調五聲
    "minor_pentatonic": [0, 3, 5, 7, 10],       # 小調五聲
}

# 每小節的節奏型（單位：拍，總和為 4，配合 4/4 拍）
RHYTHM_PATTERNS = [
    [1, 1, 1, 1],
    [0.5, 0.5, 1, 1, 1],
    [1, 0.5, 0.5, 1, 1],
    [1, 1, 0.5, 0.5, 1],
    [1, 1, 1, 0.5, 0.5],
    [2, 1, 1],
    [1, 1, 2],
    [0.5, 0.5, 0.5, 0.5, 1, 1],
    [1.5, 0.5, 1, 1],
    [2, 2],
]


def parse_note_name(name: str) -> int:
    """把音名（例如 'C', 'F#', 'Bb'）轉成 pitch class 0-11。"""
    key = name.strip().upper().replace("♯", "#").replace("♭", "B")
    if key not in NOTE_NAME_TO_PC:
        raise ValueError(f"無法辨識的音名：{name}")
    return NOTE_NAME_TO_PC[key]


def build_scale_pitches(
    root_pc: int,
    intervals: List[int],
    low: int = 60,
    high: int = 84,
) -> List[int]:
    """在 [low, high] 的 MIDI 範圍內，列出所有屬於這個音階的音高（由低到高）。"""
    pcs = {(root_pc + iv) % 12 for iv in intervals}
    return [m for m in range(low, high + 1) if m % 12 in pcs]


def generate_melody(
    root: str = "C",
    scale_type: str = "major",
    custom_notes: Optional[List[str]] = None,
    bpm: float = 90.0,
    num_bars: int = 4,
    seed: Optional[int] = None,
) -> dict:
    """
    生成旋律。

    Args:
        root: 根音音名，例如 "C"、"F#"
        scale_type: major / minor / major_pentatonic / minor_pentatonic / custom
        custom_notes: scale_type == "custom" 時使用，例如 ["C", "D", "E", "G", "A"]
        bpm: 速度
        num_bars: 小節數（4/4 拍）
        seed: 隨機種子（相同 seed 會生成相同旋律，方便重現）

    Returns:
        {"notes": [{"start", "end", "midi", "velocity"}, ...], "bpm": float, "key": str}
    """
    rng = random.Random(seed)

    root_pc = parse_note_name(root)

    if scale_type == "custom":
        if not custom_notes:
            raise ValueError("自訂音階時 custom_notes 不可為空")
        pcs = []
        for n in custom_notes:
            pc = parse_note_name(n)
            if pc not in pcs:
                pcs.append(pc)
        intervals = [(pc - root_pc) % 12 for pc in pcs]
        intervals = sorted(set(intervals))
    elif scale_type in SCALE_INTERVALS:
        intervals = SCALE_INTERVALS[scale_type]
    else:
        raise ValueError(f"不支援的音階類型：{scale_type}")

    scale_pitches = build_scale_pitches(root_pc, intervals, low=60, high=84)
    if len(scale_pitches) < 3:
        raise ValueError("音階可用的音太少，無法生成旋律")

    # 音階內「穩定音」：主音（樂句結尾偏好落在這些音上）
    tonic_pitches = [m for m in scale_pitches if m % 12 == root_pc]
    # 起始音：靠近範圍中間的主音
    mid = (scale_pitches[0] + scale_pitches[-1]) / 2
    start_pitch = min(tonic_pitches, key=lambda m: abs(m - mid)) if tonic_pitches else scale_pitches[len(scale_pitches) // 2]

    beat_sec = 60.0 / bpm
    notes = []
    idx = scale_pitches.index(start_pitch)
    current_time = 0.0

    for bar in range(num_bars):
        pattern = rng.choice(RHYTHM_PATTERNS)
        is_last_bar = bar == num_bars - 1

        # 最後一小節用簡單節奏，留空間收尾
        if is_last_bar:
            pattern = rng.choice([[2, 2], [1, 1, 2], [2, 1, 1]])

        for i, dur_beats in enumerate(pattern):
            is_final_note = is_last_bar and i == len(pattern) - 1
            is_phrase_end = (not is_final_note) and (bar % 2 == 1) and i == len(pattern) - 1

            if is_final_note:
                # 收尾：回到最近的主音
                if tonic_pitches:
                    idx = scale_pitches.index(
                        min(tonic_pitches, key=lambda m: abs(m - scale_pitches[idx]))
                    )
            else:
                # 隨機漫步：以級進為主，偶爾跳進
                step = rng.choices(
                    population=[-4, -3, -2, -1, 0, 1, 2, 3, 4],
                    weights=[2, 4, 14, 22, 8, 22, 14, 4, 2],
                )[0]
                idx = max(0, min(len(scale_pitches) - 1, idx + step))

                # 偶數小節結尾傾向靠近主音，讓樂句有段落感
                if is_phrase_end and tonic_pitches and rng.random() < 0.6:
                    idx = scale_pitches.index(
                        min(tonic_pitches, key=lambda m: abs(m - scale_pitches[idx]))
                    )

            dur_sec = dur_beats * beat_sec
            # 稍微斷開音符，聽起來比較自然（保留 90% 長度）
            note_len = dur_sec * (1.0 if is_final_note else 0.9)

            # 力度：正拍稍強
            beat_pos = sum(pattern[:i])
            on_downbeat = abs(beat_pos - round(beat_pos)) < 1e-6 and int(round(beat_pos)) % 2 == 0
            velocity = rng.randint(88, 100) if on_downbeat else rng.randint(76, 90)

            notes.append(
                {
                    "start": round(current_time, 4),
                    "end": round(current_time + note_len, 4),
                    "midi": scale_pitches[idx],
                    "velocity": velocity,
                }
            )
            current_time += dur_sec

    key_name = root.strip().upper()
    return {"notes": notes, "bpm": bpm, "key": key_name}
