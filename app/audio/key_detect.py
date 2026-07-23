"""
調性檢測 - 簡化版，根據音符分布推斷調性
"""

# MIDI note 到音名的對應（C=0, C#=1, ..., B=11）
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# 大調音階的相對音程（以 C 大調為例）
MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]  # C, D, E, F, G, A, B

# 常見調性的音符分布權重（簡化版）
KEY_PROFILES = {
    "C": [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    "G": [5.95, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    "F": [5.95, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    "D": [5.95, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    "A": [5.95, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    "E": [5.95, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
}


def detect_key(notes: list) -> str:
    """
    根據音符列表推斷調性
    
    Args:
        notes: 音符列表 [{"midi": int, ...}]
    
    Returns:
        調性字串（如 "C", "G", "F" 等）
    """
    if not notes:
        return "C"  # 預設 C 大調
    
    # 統計每個音級（pitch class）的出現頻率
    pitch_class_counts = [0] * 12
    
    for note in notes:
        midi = note.get("midi", 60)
        pitch_class = midi % 12
        # 根據音符長度加權
        duration = note.get("end", 0) - note.get("start", 0)
        pitch_class_counts[pitch_class] += duration
    
    # 簡化版：找到最常出現的音級，假設它是主音
    max_count = max(pitch_class_counts)
    if max_count == 0:
        return "C"
    
    # 找到最常出現的音級
    most_common_pitch_class = pitch_class_counts.index(max_count)
    
    # 將音級轉換為調性名稱（簡化：只考慮大調）
    key_name = NOTE_NAMES[most_common_pitch_class]
    
    # 常見調性列表（優先順序）
    common_keys = ["C", "G", "F", "D", "A", "E", "Bb", "Eb"]
    
    # 如果檢測到的調性不在常見列表中，選擇最接近的
    if key_name not in common_keys:
        # 選擇最接近的常見調性
        key_name = "C"
    
    return key_name
