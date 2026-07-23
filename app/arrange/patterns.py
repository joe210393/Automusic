"""
編曲 Pattern 定義 - Pop/Education 風格
"""

# Pop/Education 風格的 Pattern 定義
POP_PATTERN = {
    "drums": {
        "kick": [1, 3],      # 第 1, 3 拍
        "snare": [2, 4],     # 第 2, 4 拍
        "hihat": "8th"       # 8 分音符
    },
    "bass": {
        "root_on": [1, 3],   # 第 1, 3 拍彈根音
        "fifth_on": [2, 4],  # 第 2, 4 拍可選五度
        "pattern": "simple"  # 簡單模式
    },
    "harmony": {
        "type": "whole",     # 整拍和弦（先做整拍，分解和弦可後續擴展）
        "on_beat": 1         # 每小節第 1 拍
    }
}


def get_drum_pattern(bar_duration: float, beats_per_bar: int = 4) -> list:
    """
    生成鼓組 Pattern（MIDI 事件）
    
    Args:
        bar_duration: 小節時長（秒）
        beats_per_bar: 每小節拍數（預設 4）
    
    Returns:
        鼓組事件列表 [{"time": float, "note": int, "velocity": int, "duration": float}]
    """
    beat_duration = bar_duration / beats_per_bar
    events = []
    
    # Kick (MIDI note 36, C2)
    for beat in POP_PATTERN["drums"]["kick"]:
        time = (beat - 1) * beat_duration
        events.append({
            "time": time,
            "note": 36,
            "velocity": 100,
            "duration": 0.1
        })
    
    # Snare (MIDI note 38, D2)
    for beat in POP_PATTERN["drums"]["snare"]:
        time = (beat - 1) * beat_duration
        events.append({
            "time": time,
            "note": 38,
            "velocity": 90,
            "duration": 0.1
        })
    
    # Hi-hat (MIDI note 42, F#2) - 8 分音符
    if POP_PATTERN["drums"]["hihat"] == "8th":
        num_hihats = beats_per_bar * 2
        for i in range(num_hihats):
            time = i * (beat_duration / 2)
            events.append({
                "time": time,
                "note": 42,
                "velocity": 70,
                "duration": 0.05
            })
    
    return events


def get_bass_pattern(chord_degree: str, key: str, bar_duration: float, beats_per_bar: int = 4) -> list:
    """
    生成 Bass Pattern
    
    Args:
        chord_degree: 和弦級數（如 "I", "V"）
        key: 調性
        bar_duration: 小節時長（秒）
        beats_per_bar: 每小節拍數
    
    Returns:
        Bass 事件列表
    """
    from app.arrange.chords import get_chord_notes
    
    beat_duration = bar_duration / beats_per_bar
    events = []
    
    # 取得和弦的根音和五度
    chord_notes = get_chord_notes(key, chord_degree)
    root_note = chord_notes[0] - 24  # 降低兩個八度到 Bass 範圍
    fifth_note = chord_notes[1] - 24 if len(chord_notes) > 1 else root_note + 7
    
    # 第 1, 3 拍彈根音
    for beat in POP_PATTERN["bass"]["root_on"]:
        time = (beat - 1) * beat_duration
        events.append({
            "time": time,
            "note": root_note,
            "velocity": 90,
            "duration": beat_duration * 0.8
        })
    
    # 第 2, 4 拍可選五度
    if POP_PATTERN["bass"]["fifth_on"]:
        for beat in POP_PATTERN["bass"]["fifth_on"]:
            time = (beat - 1) * beat_duration
            events.append({
                "time": time,
                "note": fifth_note,
                "velocity": 85,
                "duration": beat_duration * 0.8
            })
    
    return events


def get_harmony_pattern(chord_degree: str, key: str, bar_duration: float) -> list:
    """
    生成和聲 Pattern（整拍和弦）
    
    Args:
        chord_degree: 和弦級數
        key: 調性
        bar_duration: 小節時長（秒）
    
    Returns:
        和聲事件列表
    """
    from app.arrange.chords import get_chord_notes
    
    events = []
    
    # 取得和弦的三個音
    chord_notes = get_chord_notes(key, chord_degree)
    
    # 整拍和弦：每小節第 1 拍同時彈三個音
    if POP_PATTERN["harmony"]["type"] == "whole":
        for note in chord_notes:
            events.append({
                "time": 0.0,
                "note": note,
                "velocity": 80,
                "duration": bar_duration * 0.9
            })
    
    return events
