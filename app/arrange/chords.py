"""
和弦生成 - 根據調性和旋律音符推斷和弦
使用規則法，不使用機器學習
"""

# 大調音階的音級對應：支援全部 12 個調
_MAJOR_STEPS = [0, 2, 4, 5, 7, 9, 11]
_KEY_ROOT_PC = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
    "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8,
    "A": 9, "A#": 10, "Bb": 10, "B": 11,
}
MAJOR_SCALE_DEGREES = {
    name: [(root + step) % 12 for step in _MAJOR_STEPS]
    for name, root in _KEY_ROOT_PC.items()
}

# 和弦根音相對調性主音的半音數（支援調式／副屬變化）
CHORD_ROOT_OFFSET = {
    "I": 0,
    "bII": 1,
    "ii": 2,
    "bIII": 3,
    "iii": 4,
    "IV": 5,
    "V": 7,
    "bVI": 8,
    "vi": 9,
    "bVII": 10,
    "vii": 11,
    # 七和弦（根音同三和弦）
    "Imaj7": 0,
    "ii7": 2,
    "iii7": 4,
    "IVmaj7": 5,
    "V7": 7,
    "vi7": 9,
    "vii7": 11,
}

# 和弦音程（相對該和弦根音）
CHORD_TYPES = {
    "I": [0, 4, 7],
    "bII": [0, 4, 7],
    "ii": [0, 3, 7],
    "bIII": [0, 4, 7],
    "iii": [0, 3, 7],
    "IV": [0, 4, 7],
    "V": [0, 4, 7],
    "bVI": [0, 4, 7],
    "vi": [0, 3, 7],
    "bVII": [0, 4, 7],
    "vii": [0, 3, 6],  # 減三和弦
    "Imaj7": [0, 4, 7, 11],
    "ii7": [0, 3, 7, 10],
    "iii7": [0, 3, 7, 10],
    "IVmaj7": [0, 4, 7, 11],
    "V7": [0, 4, 7, 10],
    "vi7": [0, 3, 7, 10],
    "vii7": [0, 3, 6, 10],
}

# 向下相容：舊程式用音階位置查表
CHORD_SCALE_POSITIONS = {
    "I": 0, "ii": 1, "iii": 2, "IV": 3, "V": 4, "vi": 5, "vii": 6,
}

# 和弦優先順序（用於逐音推斷）
CHORD_PRIORITY = [
    "I", "V", "vi", "IV", "ii", "iii", "V7", "Imaj7", "ii7", "vi7",
    "bVII", "bVI", "vii",
]


def get_chord_notes(key: str, chord_degree: str) -> list:
    """
    根據調性和和弦級數取得 MIDI 音符列表（C4=60 附近）。
    七和弦回傳 4 音；三和弦回傳 3 音。
    """
    if key not in MAJOR_SCALE_DEGREES:
        key = "C"

    key_root = _KEY_ROOT_PC.get(key, 0)
    if chord_degree not in CHORD_TYPES:
        chord_degree = "I"

    root_pc = (key_root + CHORD_ROOT_OFFSET.get(chord_degree, 0)) % 12
    chord_intervals = CHORD_TYPES[chord_degree]

    chord_notes = []
    for interval in chord_intervals:
        pitch_class = (root_pc + interval) % 12
        midi_note = 60 + pitch_class
        if pitch_class < root_pc:
            midi_note += 12
        chord_notes.append(midi_note)

    chord_notes.sort()
    return chord_notes


def infer_chords(notes: list, key: str, bpm: float, time_signature: tuple = (4, 4)) -> list:
    """根據旋律音符推斷每小節的和弦。"""
    beats_per_second = bpm / 60.0
    beats_per_bar = time_signature[0]
    bar_duration = beats_per_bar / beats_per_second

    if not notes:
        return []

    max_time = max(note.get("end", 0) for note in notes)
    num_bars = int(max_time / bar_duration) + 1

    chords = []

    for bar in range(num_bars):
        bar_start = bar * bar_duration
        bar_end = (bar + 1) * bar_duration

        bar_notes = [
            note for note in notes
            if bar_start <= note.get("start", 0) < bar_end
            or bar_start < note.get("end", 0) <= bar_end
            or (note.get("start", 0) < bar_start and note.get("end", 0) > bar_end)
        ]

        if not bar_notes:
            chord_degree = chords[-1]["chord"] if chords else "I"
        else:
            note_durations = {}
            for note in bar_notes:
                midi = note.get("midi", 60)
                pitch_class = midi % 12
                duration = min(note.get("end", 0), bar_end) - max(note.get("start", 0), bar_start)
                note_durations[pitch_class] = note_durations.get(pitch_class, 0) + duration

            if note_durations:
                longest_pitch_class = max(note_durations, key=note_durations.get)
                chord_degree = find_chord_containing_note(key, longest_pitch_class)
            else:
                chord_degree = "I"

        if bar == num_bars - 1:
            chord_degree = "I"

        chords.append({
            "bar": bar,
            "chord": chord_degree,
            "start": bar_start,
            "end": bar_end,
        })

    return chords


def get_chord_pitch_classes(key: str, chord_degree: str) -> set:
    """取得某調性中某和弦級數的音級（pitch class 集合）。"""
    notes = get_chord_notes(key, chord_degree)
    return {n % 12 for n in notes}


def select_chords_for_melody(
    notes: list,
    key: str,
    bpm: float,
    num_bars: int,
    time_signature: tuple = (4, 4),
    style: str = None,
    seed: int = None,
) -> list:
    """
    為旋律配和弦：依風格從樂理資料庫挑一組進行（同風格有多組可換）。
    回傳和弦級數列表，例如 ["I", "V", "vi", "IV"]。
    """
    from app.theory.knowledge import best_progression_for_melody

    result = best_progression_for_melody(
        notes, key, bpm, num_bars, time_signature,
        style=style, seed=seed,
    )
    return result["degrees"]


def find_chord_containing_note(key: str, pitch_class: int) -> str:
    """找出包含指定音級的和弦（按優先順序）。"""
    for chord_degree in CHORD_PRIORITY:
        if pitch_class in get_chord_pitch_classes(key, chord_degree):
            return chord_degree
    return "I"
