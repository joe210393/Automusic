"""
地端旋律生成器

輸入音階（內建音階或自訂音名），完全在本機用演算法生成旋律：
- 音高：樂句拱形＋動機重複，級進為主、強拍偏向穩定音
- 節奏：更多樣的節奏型（含切分、休止暗示）
- 收尾：最後一個音強制回到主音並拉長，聽起來有結束感

不需要任何外部 API 或模型。
"""

import random
from typing import List, Optional, Tuple

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
# 含較多長短對比與切分，避免四平八穩的「練習曲感」
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
    [0.5, 1.5, 1, 1],
    [1, 0.5, 1.5, 1],
    [1.5, 0.5, 0.5, 0.5, 1],
    [0.75, 0.25, 1, 0.5, 0.5, 1],
    [2, 0.5, 0.5, 1],
    [1, 2, 1],
    [0.5, 0.5, 2, 1],
    [1.5, 1.5, 1],
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
    low: int = 55,
    high: int = 79,
) -> List[int]:
    """在 [low, high] 的 MIDI 範圍內，列出所有屬於這個音階的音高（由低到高）。"""
    pcs = {(root_pc + iv) % 12 for iv in intervals}
    return [m for m in range(low, high + 1) if m % 12 in pcs]


def _stable_degrees(intervals: List[int]) -> set:
    """主音、三度、五度（若在音階內）視為穩定音。"""
    want = {0, 4, 7, 3}  # 大/小三、五
    return {iv for iv in intervals if iv in want or iv == 0}


def _pick_near(
    scale_pitches: List[int],
    target: int,
    prefer_pcs: Optional[set] = None,
) -> int:
    cands = scale_pitches
    if prefer_pcs:
        filtered = [m for m in scale_pitches if (m % 12) in prefer_pcs]
        if filtered:
            cands = filtered
    return min(cands, key=lambda m: abs(m - target))


def _generate_bar_contour(
    rng: random.Random,
    scale_pitches: List[int],
    start_idx: int,
    pattern: List[float],
    *,
    tonic_pcs: set,
    stable_pcs: set,
    phrase_role: str,
) -> Tuple[List[int], int]:
    """
    產生一小節的音高索引序列。
    phrase_role: open / rise / fall / close
    """
    idxs = []
    idx = start_idx
    n = len(pattern)
    for i, _dur in enumerate(pattern):
        beat_pos = sum(pattern[:i])
        on_strong = abs(beat_pos - round(beat_pos)) < 1e-6 and int(round(beat_pos)) % 2 == 0
        is_last = i == n - 1

        if phrase_role == "close" and is_last:
            target = _pick_near(scale_pitches, scale_pitches[idx], tonic_pcs)
            idx = scale_pitches.index(target)
        elif phrase_role == "open" and is_last:
            # 半終止感：落到五度或三度
            prefer = stable_pcs - tonic_pcs or stable_pcs
            target = _pick_near(scale_pitches, scale_pitches[idx], prefer)
            idx = scale_pitches.index(target)
        else:
            # 拱形：前半偏上、後半偏下
            if phrase_role == "rise":
                weights = [1, 2, 6, 18, 6, 28, 22, 10, 7]
            elif phrase_role == "fall":
                weights = [7, 10, 22, 28, 6, 18, 6, 2, 1]
            else:
                weights = [2, 4, 14, 26, 6, 26, 14, 5, 3]
            step = rng.choices(
                population=[-4, -3, -2, -1, 0, 1, 2, 3, 4],
                weights=weights,
            )[0]
            # 強拍更常落在穩定音：先走一步再吸附
            idx = max(0, min(len(scale_pitches) - 1, idx + step))
            if on_strong and rng.random() < 0.55:
                target = _pick_near(scale_pitches, scale_pitches[idx], stable_pcs)
                idx = scale_pitches.index(target)
            # 避免同一音連敲超過兩次
            if len(idxs) >= 2 and idxs[-1] == idxs[-2] == idx:
                step2 = 1 if rng.random() < 0.5 else -1
                idx = max(0, min(len(scale_pitches) - 1, idx + step2))

        idxs.append(idx)
    return idxs, idx


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

    # 音域壓在舒適歌唱／樂器區，避免編成歌曲後出現刺耳高音
    scale_pitches = build_scale_pitches(root_pc, intervals, low=55, high=79)
    if len(scale_pitches) < 3:
        raise ValueError("音階可用的音太少，無法生成旋律")

    tonic_pcs = {root_pc}
    stable_iv = _stable_degrees(intervals)
    stable_pcs = {(root_pc + iv) % 12 for iv in stable_iv} | tonic_pcs

    tonic_pitches = [m for m in scale_pitches if m % 12 == root_pc]
    mid = (scale_pitches[0] + scale_pitches[-1]) / 2
    # 起始略低於中間，留給後面上行空間
    start_target = mid - 4
    start_pitch = (
        min(tonic_pitches, key=lambda m: abs(m - start_target))
        if tonic_pitches
        else scale_pitches[len(scale_pitches) // 3]
    )

    beat_sec = 60.0 / bpm
    notes = []
    idx = scale_pitches.index(start_pitch)
    current_time = 0.0

    # 先做「動機」小節，後續重複／變奏，聽起來更像完整旋律
    motif_pattern = rng.choice(RHYTHM_PATTERNS)
    motif_idxs, motif_end_idx = _generate_bar_contour(
        rng, scale_pitches, idx, motif_pattern,
        tonic_pcs=tonic_pcs, stable_pcs=stable_pcs, phrase_role="open",
    )

    # 每 4 小節一組：動機 → 上揚 → 動機變奏 → 收束
    roles_cycle = ["open", "rise", "open", "close"]

    for bar in range(num_bars):
        is_last_bar = bar == num_bars - 1
        role = "close" if is_last_bar else roles_cycle[bar % 4]

        if bar == 0:
            pattern, bar_idxs = motif_pattern, list(motif_idxs)
            idx = motif_end_idx
        elif role == "open" and bar % 4 == 2 and rng.random() < 0.75:
            # 重複動機節奏，音高做小變奏（平移 ±1～2 級）
            pattern = motif_pattern
            shift = rng.choice([-2, -1, 1, 2])
            bar_idxs = [
                max(0, min(len(scale_pitches) - 1, i + shift))
                for i in motif_idxs
            ]
            # 結尾拉回穩定音
            end_pitch = _pick_near(scale_pitches, scale_pitches[bar_idxs[-1]], stable_pcs)
            bar_idxs[-1] = scale_pitches.index(end_pitch)
            idx = bar_idxs[-1]
        else:
            if is_last_bar:
                pattern = rng.choice([[2, 2], [1, 1, 2], [2, 1, 1], [1.5, 0.5, 2]])
            else:
                pattern = rng.choice(RHYTHM_PATTERNS)
            bar_idxs, idx = _generate_bar_contour(
                rng, scale_pitches, idx, pattern,
                tonic_pcs=tonic_pcs, stable_pcs=stable_pcs, phrase_role=role,
            )

        for i, dur_beats in enumerate(pattern):
            is_final_note = is_last_bar and i == len(pattern) - 1
            pitch_idx = bar_idxs[i]
            dur_sec = dur_beats * beat_sec
            # 長音多留一點，短音稍微斷開
            legato = 0.96 if dur_beats >= 1.5 else 0.88
            note_len = dur_sec * (1.0 if is_final_note else legato)

            beat_pos = sum(pattern[:i])
            on_downbeat = abs(beat_pos - round(beat_pos)) < 1e-6 and int(round(beat_pos)) % 2 == 0
            velocity = rng.randint(86, 98) if on_downbeat else rng.randint(72, 88)
            if is_final_note:
                velocity = max(velocity, 90)

            notes.append(
                {
                    "start": round(current_time, 4),
                    "end": round(current_time + note_len, 4),
                    "midi": scale_pitches[pitch_idx],
                    "velocity": velocity,
                }
            )
            current_time += dur_sec

    key_name = root.strip().upper()
    return {"notes": notes, "bpm": bpm, "key": key_name}
