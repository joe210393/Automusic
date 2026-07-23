"""
和弦生成 - 根據調性和旋律音符推斷和弦
使用規則法，不使用機器學習
"""

# 大調音階的音級對應（0=C, 1=D, 2=E, ...）
MAJOR_SCALE_DEGREES = {
    "C": [0, 2, 4, 5, 7, 9, 11],  # C, D, E, F, G, A, B
    "G": [7, 9, 11, 0, 2, 4, 6],  # G, A, B, C, D, E, F#
    "F": [5, 7, 9, 10, 0, 2, 4],  # F, G, A, Bb, C, D, E
    "D": [2, 4, 6, 7, 9, 11, 1],  # D, E, F#, G, A, B, C#
    "A": [9, 11, 1, 2, 4, 6, 8],  # A, B, C#, D, E, F#, G#
    "E": [4, 6, 8, 9, 11, 1, 3],  # E, F#, G#, A, B, C#, D#
    "Bb": [10, 0, 2, 3, 5, 7, 9], # Bb, C, D, Eb, F, G, A
    "Eb": [3, 5, 7, 8, 10, 0, 2], # Eb, F, G, Ab, Bb, C, D
}

# 和弦類型（大調）- 相對於根音的半音數
CHORD_TYPES = {
    "I": [0, 4, 7],      # 大三和弦（根音、大三度、純五度）
    "ii": [0, 3, 7],     # 小三和弦（根音、小三度、純五度）
    "iii": [0, 3, 7],    # 小三和弦
    "IV": [0, 4, 7],     # 大三和弦
    "V": [0, 4, 7],      # 大三和弦
    "vi": [0, 3, 7],     # 小三和弦
}

# 和弦在音階中的位置（0-based index）
CHORD_SCALE_POSITIONS = {
    "I": 0,    # 第 1 個音
    "ii": 1,   # 第 2 個音
    "iii": 2,  # 第 3 個音
    "IV": 3,   # 第 4 個音
    "V": 4,    # 第 5 個音
    "vi": 5,   # 第 6 個音
}

# 和弦優先順序（用於選擇）
CHORD_PRIORITY = ["I", "V", "vi", "IV", "ii", "iii"]


def get_chord_notes(key: str, chord_degree: str) -> list:
    """
    根據調性和和弦級數取得 MIDI 音符列表
    
    Args:
        key: 調性（如 "C"）
        chord_degree: 和弦級數（如 "I", "V", "vi"）
    
    Returns:
        MIDI 音符列表（以 C4=60 為基準）
    """
    if key not in MAJOR_SCALE_DEGREES:
        key = "C"  # 預設 C 大調
    
    scale_degrees = MAJOR_SCALE_DEGREES[key]
    
    if chord_degree not in CHORD_TYPES:
        chord_degree = "I"  # 預設 I 級
    
    # 取得和弦根音在音階中的位置
    scale_position = CHORD_SCALE_POSITIONS.get(chord_degree, 0)
    root_pitch_class = scale_degrees[scale_position]
    
    # 取得和弦類型（相對於根音的半音數）
    chord_intervals = CHORD_TYPES[chord_degree]
    
    # 計算和弦的三個音
    chord_notes = []
    for interval in chord_intervals:
        # 計算音級（相對於根音）
        pitch_class = (root_pitch_class + interval) % 12
        # 轉換為 MIDI（C4=60）
        midi_note = 60 + pitch_class
        # 如果音級小於根音，需要加一個八度
        if pitch_class < root_pitch_class:
            midi_note += 12
        chord_notes.append(midi_note)
    
    # 確保音符按順序排列
    chord_notes.sort()
    
    return chord_notes


def infer_chords(notes: list, key: str, bpm: float, time_signature: tuple = (4, 4)) -> list:
    """
    根據旋律音符推斷每小節的和弦
    
    Args:
        notes: 音符列表
        key: 調性
        bpm: 節拍速度
        time_signature: 拍號（預設 4/4）
    
    Returns:
        和弦列表 [{"bar": int, "chord": str, "start": float, "end": float}]
    """
    beats_per_second = bpm / 60.0
    beats_per_bar = time_signature[0]
    bar_duration = beats_per_bar / beats_per_second
    
    # 找出總時長
    if not notes:
        return []
    
    max_time = max(note.get("end", 0) for note in notes)
    num_bars = int(max_time / bar_duration) + 1
    
    chords = []
    
    for bar in range(num_bars):
        bar_start = bar * bar_duration
        bar_end = (bar + 1) * bar_duration
        
        # 找出這個小節內的所有音符
        bar_notes = [
            note for note in notes
            if bar_start <= note.get("start", 0) < bar_end
            or bar_start < note.get("end", 0) <= bar_end
            or (note.get("start", 0) < bar_start and note.get("end", 0) > bar_end)
        ]
        
        if not bar_notes:
            # 如果小節內沒有音符，使用前一個小節的和弦，或預設 I
            if chords:
                chord_degree = chords[-1]["chord"]
            else:
                chord_degree = "I"
        else:
            # 找出最長音或最常出現的音
            note_durations = {}
            for note in bar_notes:
                midi = note.get("midi", 60)
                pitch_class = midi % 12
                duration = min(note.get("end", 0), bar_end) - max(note.get("start", 0), bar_start)
                
                if pitch_class not in note_durations:
                    note_durations[pitch_class] = 0
                note_durations[pitch_class] += duration
            
            if note_durations:
                # 找出持續時間最長的音級
                longest_pitch_class = max(note_durations, key=note_durations.get)
                
                # 找出包含這個音級的和弦
                chord_degree = find_chord_containing_note(key, longest_pitch_class)
            else:
                chord_degree = "I"
        
        # 最後一小節強制回 I
        if bar == num_bars - 1:
            chord_degree = "I"
        
        chords.append({
            "bar": bar,
            "chord": chord_degree,
            "start": bar_start,
            "end": bar_end
        })
    
    return chords


def find_chord_containing_note(key: str, pitch_class: int) -> str:
    """
    找出包含指定音級的和弦（按優先順序）
    
    Args:
        key: 調性
        pitch_class: 音級（0-11）
    
    Returns:
        和弦級數（如 "I", "V"）
    """
    if key not in MAJOR_SCALE_DEGREES:
        key = "C"
    
    scale_degrees = MAJOR_SCALE_DEGREES[key]
    
    # 檢查每個和弦是否包含這個音級
    for chord_degree in CHORD_PRIORITY:
        chord_intervals = CHORD_TYPES[chord_degree]
        scale_position = CHORD_SCALE_POSITIONS.get(chord_degree, 0)
        root_pitch_class = scale_degrees[scale_position]
        
        # 計算和弦的所有音級
        chord_pitch_classes = [(root_pitch_class + interval) % 12 for interval in chord_intervals]
        
        if pitch_class in chord_pitch_classes:
            return chord_degree
    
    # 如果找不到，返回 I
    return "I"
