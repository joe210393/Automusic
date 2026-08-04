"""
MIDI 生成 - 將音符、和弦、Pattern 組合成一首有結構的歌

歌曲結構：
    前奏（1 小節，伴奏先進）
    → 主旋律 A（旋律 + 完整伴奏）
    → 主旋律 A'（旋律重複一次，聽起來像完整的一段）
    → 尾奏（1 小節，收在主和弦）

旋律貫穿整首歌，不會出現「旋律播完了伴奏還在空跑」的狀況。
"""

import math
import random
import mido
from mido import MidiFile, MidiTrack, Message, MetaMessage
import os
from typing import List, Optional

# GM 樂器（0-indexed program number）：每次編曲隨 seed 挑選，音色有變化
MELODY_PROGRAM_CHOICES = [0, 4, 11, 24]   # 鋼琴 / 電鋼琴 / 顫音琴 / 尼龍吉他
HARMONY_PROGRAM_CHOICES = [4, 0, 48]      # 電鋼琴 / 鋼琴 / 弦樂鋪底
BASS_PROGRAM = 33                          # Electric Bass (finger)

CRASH = 49  # Crash Cymbal 1


def compute_song_structure(notes: list, bpm: float) -> dict:
    """
    計算歌曲結構（前奏/主旋律小節數/重複次數/尾奏）。
    /render-audio 混入原始錄音時也用這個函式，確保時間軸一致。
    """
    from app.audio.quantize import quantize_notes

    quantized = quantize_notes(notes, bpm, grid="1/8")
    beats_per_second = bpm / 60.0
    bar_duration = 4.0 / beats_per_second

    melody_end = max((n["end"] for n in quantized), default=0.0)
    melody_bars = max(1, math.ceil(melody_end / bar_duration - 1e-6))
    melody_bars = min(melody_bars, 16)  # 安全上限
    repeats = 2 if melody_bars <= 8 else 1

    # 成品控制在 1 分鐘內
    MAX_SONG_SECONDS = 60.0
    def total_seconds(mb, rp):
        return (1 + mb * rp + 1) * bar_duration
    if total_seconds(melody_bars, repeats) > MAX_SONG_SECONDS:
        repeats = 1
    if total_seconds(melody_bars, repeats) > MAX_SONG_SECONDS:
        melody_bars = max(1, int(MAX_SONG_SECONDS / bar_duration) - 2)

    return {
        "intro_bars": 1,
        "melody_bars": melody_bars,
        "repeats": repeats,
        "outro_bars": 1,
        "bar_duration": bar_duration,
    }


def generate_full_midi(
    notes: list,
    bpm: float,
    key: str,
    lyrics: dict = None,
    chord_overrides: Optional[List[str]] = None,
    seed: Optional[int] = None,
    melody_gain: float = 1.0,
    style: Optional[str] = None,
) -> str:
    """
    生成完整的 MIDI 檔案（回傳檔案路徑）。
    seed 相同時，伴奏變化型與結構完全相同，方便重現。
    style 指定風格時，鼓/貝斯/和聲的變化型與樂器音色依樂理資料庫的風格定義挑選
    （例如搖籃曲：音樂盒主奏、無鼓；搖滾：電吉他、切分大鼓）。
    """
    from app.audio.quantize import quantize_notes
    from app.arrange.patterns import (
        get_drum_pattern,
        get_bass_pattern,
        get_harmony_pattern,
        NUM_DRUM_VARIATIONS,
        NUM_BASS_VARIATIONS,
        NUM_HARMONY_VARIATIONS,
        KICK,
    )
    from app.arrange.chords import get_chord_notes, select_chords_for_melody

    from app.theory.knowledge import get_style

    rng = random.Random(seed)
    style_cfg = get_style(style)
    if style_cfg:
        drum_choices = style_cfg.get("drum_variations", [])
        drum_var = rng.choice(drum_choices) if drum_choices else None  # None = 這個風格不用鼓
        bass_var = rng.choice(style_cfg.get("bass_variations") or [0])
        harmony_var = rng.choice(style_cfg.get("harmony_variations") or [0])
        melody_program = rng.choice(style_cfg.get("melody_programs") or MELODY_PROGRAM_CHOICES)
        harmony_program = rng.choice(style_cfg.get("harmony_programs") or HARMONY_PROGRAM_CHOICES)
    else:
        drum_var = rng.randrange(NUM_DRUM_VARIATIONS)
        bass_var = rng.randrange(NUM_BASS_VARIATIONS)
        harmony_var = rng.randrange(NUM_HARMONY_VARIATIONS)
        melody_program = rng.choice(MELODY_PROGRAM_CHOICES)
        harmony_program = rng.choice(HARMONY_PROGRAM_CHOICES)

    quantized_notes = quantize_notes(notes, bpm, grid="1/8")

    beats_per_second = bpm / 60.0
    bar_duration = 4.0 / beats_per_second

    # ---- 歌曲結構（與 compute_song_structure 一致） ----
    structure = compute_song_structure(notes, bpm)
    melody_bars = structure["melody_bars"]
    INTRO_BARS = structure["intro_bars"]
    REPEATS = structure["repeats"]
    OUTRO_BARS = structure["outro_bars"]

    # 超過旋律小節上限的音符裁掉
    cutoff = melody_bars * bar_duration
    quantized_notes = [n for n in quantized_notes if n["start"] < cutoff]
    for n in quantized_notes:
        n["end"] = min(n["end"], cutoff)

    # 沒有指定和弦時，依旋律內容挑最貼合的和弦（含 V→I 終止式）
    if chord_overrides is None:
        chord_overrides = select_chords_for_melody(quantized_notes, key, bpm, melody_bars)
    total_bars = INTRO_BARS + melody_bars * REPEATS + OUTRO_BARS

    # 各段主旋律的起始小節
    section_start_bars = [INTRO_BARS + r * melody_bars for r in range(REPEATS)]
    outro_bar = total_bars - OUTRO_BARS

    # ---- 旋律：重複 REPEATS 次，貫穿整首歌 ----
    full_melody = []
    for r in range(REPEATS):
        offset = (INTRO_BARS + r * melody_bars) * bar_duration
        for n in quantized_notes:
            m = dict(n)
            m["start"] = n["start"] + offset
            m["end"] = n["end"] + offset
            full_melody.append(m)
    full_melody.sort(key=lambda x: x["start"])

    # ---- 每小節的和弦 ----
    bar_chords = []
    for bar in range(total_bars):
        if bar < INTRO_BARS:
            degree = chord_overrides[0]
        elif bar >= outro_bar:
            degree = "I"
        elif bar == outro_bar - 1:
            degree = "V"  # 尾奏前放屬和弦，V→I 正格終止收尾
        else:
            mel_bar = (bar - INTRO_BARS) % melody_bars
            degree = chord_overrides[mel_bar % len(chord_overrides)]
        bar_chords.append(degree)

    # ---- 建立 MIDI ----
    mid = MidiFile()
    mid.ticks_per_beat = 480

    melody_track = MidiTrack()
    drums_track = MidiTrack()
    bass_track = MidiTrack()
    harmony_track = MidiTrack()
    for t in (melody_track, drums_track, bass_track, harmony_track):
        mid.tracks.append(t)

    tempo = mido.bpm2tempo(bpm)
    melody_track.append(MetaMessage('set_tempo', tempo=tempo, time=0))

    melody_track.append(Message('program_change', program=melody_program, channel=0, time=0))
    bass_track.append(Message('program_change', program=BASS_PROGRAM, channel=1, time=0))
    harmony_track.append(Message('program_change', program=harmony_program, channel=2, time=0))

    def sec_to_ticks(sec: float) -> int:
        return int(sec * mid.ticks_per_beat * beats_per_second)

    def write_track_events(track, channel, timed_events):
        """
        timed_events: [(start_sec, duration_sec, note, velocity), ...]
        正確處理同時發聲（和弦）與重疊音：全部轉成絕對 tick 排序後再算 delta。
        """
        msgs = []
        for start_sec, dur_sec, note, vel in timed_events:
            on = sec_to_ticks(start_sec)
            off = on + max(1, sec_to_ticks(dur_sec))
            msgs.append((on, 1, note, vel))
            msgs.append((off, 0, note, 0))
        # 同一 tick 時 note_off 先於 note_on
        msgs.sort(key=lambda x: (x[0], x[1]))
        current = 0
        for t, kind, note, vel in msgs:
            delta = max(0, t - current)
            track.append(Message('note_on' if kind else 'note_off',
                                 channel=channel, note=note, velocity=vel, time=delta))
            current = max(current, t)

    # ---- 旋律軌 ----
    # melody_gain < 1 時旋律轉為小聲跟奏（例如混入原始人聲時）
    melody_events = [
        (
            n["start"],
            n["end"] - n["start"],
            n["midi"],
            max(20, min(127, int(max(70, min(110, n.get("velocity", 90))) * melody_gain))),
        )
        for n in full_melody
    ]
    write_track_events(melody_track, 0, melody_events)

    # ---- 伴奏軌：先收集全部事件，最後一次寫入 ----
    drum_all = []
    bass_all = []
    harmony_all = []

    for bar in range(total_bars):
        bar_start = bar * bar_duration
        degree = bar_chords[bar]
        is_outro = bar >= outro_bar
        # 段落開頭放 crash；段落結尾前一小節放過門
        has_crash = (bar in section_start_bars) or is_outro
        next_is_section = (bar + 1) in section_start_bars or (bar + 1) == outro_bar
        has_fill = next_is_section and not is_outro

        # 鼓（drum_var 為 None 表示此風格不用鼓，例如搖籃曲）
        if drum_var is None:
            drum_events = []
        elif is_outro:
            drum_events = [
                {"time": 0.0, "note": KICK, "velocity": 96, "duration": 0.1},
                {"time": 0.0, "note": CRASH, "velocity": 88, "duration": 0.6},
            ]
        else:
            drum_events = get_drum_pattern(bar_duration, beats_per_bar=4, variation=drum_var,
                                           fill=has_fill, crash=has_crash)
        drum_all.extend((bar_start + e["time"], e["duration"], e["note"], e["velocity"]) for e in drum_events)

        # Bass
        if is_outro:
            root = get_chord_notes(key, "I")[0] - 24
            bass_events = [{"time": 0.0, "note": root, "velocity": 80, "duration": bar_duration * 0.95}]
        else:
            bass_events = get_bass_pattern(degree, key, bar_duration, beats_per_bar=4, variation=bass_var)
        bass_all.extend((bar_start + e["time"], e["duration"], e["note"], e["velocity"]) for e in bass_events)

        # 和聲
        if is_outro:
            harmony_events = [
                {"time": 0.0, "note": n - 12, "velocity": 60, "duration": bar_duration * 0.95}
                for n in get_chord_notes(key, "I")
            ]
        else:
            harmony_events = get_harmony_pattern(degree, key, bar_duration, variation=harmony_var)
        harmony_all.extend((bar_start + e["time"], e["duration"], e["note"], e["velocity"]) for e in harmony_events)

    write_track_events(drums_track, 9, drum_all)
    write_track_events(bass_track, 1, bass_all)
    write_track_events(harmony_track, 2, harmony_all)

    for track in (melody_track, drums_track, bass_track, harmony_track):
        track.append(MetaMessage('end_of_track', time=0))

    output_dir = "/tmp"
    os.makedirs(output_dir, exist_ok=True)
    midi_path = os.path.join(output_dir, "full.mid")
    mid.save(midi_path)

    return midi_path
