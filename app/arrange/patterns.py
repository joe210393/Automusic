"""
編曲 Pattern 定義 - Pop/Education 風格

每種樂器提供多個變化型（variation），由 generate_midi 隨機挑選，
讓每次編曲聽起來不一樣。
"""

# 鼓的 GM 音符
KICK = 36
SNARE = 38
HIHAT_CLOSED = 42

NUM_DRUM_VARIATIONS = 3
NUM_BASS_VARIATIONS = 3
NUM_HARMONY_VARIATIONS = 3


def get_drum_pattern(bar_duration: float, beats_per_bar: int = 4, variation: int = 0) -> list:
    """
    生成鼓組 Pattern（MIDI 事件）

    variation:
        0: 標準流行（kick 1,3 / snare 2,4 / hihat 8 分）
        1: 輕快（kick 1, 3.5 切分 / snare 2,4 / hihat 8 分）
        2: 簡約（kick 1,3 / snare 2,4，只有 4 分 hihat，適合慢歌）
    """
    beat = bar_duration / beats_per_bar
    events = []

    def hit(time_beats: float, note: int, velocity: int, duration: float = 0.08):
        events.append({
            "time": time_beats * beat,
            "note": note,
            "velocity": velocity,
            "duration": duration,
        })

    variation = variation % NUM_DRUM_VARIATIONS

    if variation == 0:
        kick_beats = [0, 2]
        hihat_step = 0.5
    elif variation == 1:
        kick_beats = [0, 2.5]
        hihat_step = 0.5
    else:
        kick_beats = [0, 2]
        hihat_step = 1.0

    for b in kick_beats:
        hit(b, KICK, 96)
    for b in [1, 3]:
        hit(b, SNARE, 84)

    t = 0.0
    while t < beats_per_bar - 1e-6:
        # hihat 放輕，只當節奏背景
        hit(t, HIHAT_CLOSED, 45, duration=0.04)
        t += hihat_step

    return events


def get_bass_pattern(
    chord_degree: str,
    key: str,
    bar_duration: float,
    beats_per_bar: int = 4,
    variation: int = 0,
) -> list:
    """
    生成 Bass Pattern

    variation:
        0: 根音 1,3 拍 + 五度 2,4 拍（標準）
        1: 根音長音（1 拍起彈滿半小節 ×2）
        2: 根音-根音-五度-八度（流行行進感）
    """
    from app.arrange.chords import get_chord_notes

    beat = bar_duration / beats_per_bar
    chord_notes = get_chord_notes(key, chord_degree)
    root = chord_notes[0] - 24   # 降兩個八度到 Bass 音域
    fifth = root + 7
    octave = root + 12

    variation = variation % NUM_BASS_VARIATIONS
    events = []

    def play(time_beats: float, note: int, dur_beats: float, velocity: int = 78):
        events.append({
            "time": time_beats * beat,
            "note": note,
            "velocity": velocity,
            "duration": dur_beats * beat * 0.9,
        })

    if variation == 0:
        play(0, root, 1)
        play(1, fifth, 1, velocity=70)
        play(2, root, 1)
        play(3, fifth, 1, velocity=70)
    elif variation == 1:
        play(0, root, 2)
        play(2, root, 2)
    else:
        play(0, root, 1)
        play(1, root, 1, velocity=72)
        play(2, fifth, 1, velocity=72)
        play(3, octave, 1, velocity=74)

    return events


def get_harmony_pattern(
    chord_degree: str,
    key: str,
    bar_duration: float,
    variation: int = 0,
) -> list:
    """
    生成和聲 Pattern。和弦降一個八度（48-60 附近），避開旋律音域（60-84）。

    variation:
        0: 每小節一次長和弦（柔和鋪底）
        1: 分解和弦（8 分音符上行-下行）
        2: 半小節刷兩次和弦
    """
    from app.arrange.chords import get_chord_notes

    chord_notes = [n - 12 for n in get_chord_notes(key, chord_degree)]
    beat = bar_duration / 4.0

    variation = variation % NUM_HARMONY_VARIATIONS
    events = []

    if variation == 0:
        for note in chord_notes:
            events.append({
                "time": 0.0,
                "note": note,
                "velocity": 55,
                "duration": bar_duration * 0.95,
            })
    elif variation == 1:
        # 分解和弦：根-三-五-三 循環，8 分音符
        seq = [chord_notes[0], chord_notes[1], chord_notes[2], chord_notes[1]] * 2
        for i, note in enumerate(seq):
            events.append({
                "time": i * beat * 0.5,
                "note": note,
                "velocity": 58,
                "duration": beat * 0.45,
            })
    else:
        for start_beat in (0, 2):
            for note in chord_notes:
                events.append({
                    "time": start_beat * beat,
                    "note": note,
                    "velocity": 55,
                    "duration": beat * 1.8,
                })

    return events
