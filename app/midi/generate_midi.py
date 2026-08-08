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

# 木管／哨笛類：強制收進舒適音域，避免成品出現刺耳高頻
WOODWIND_LIKE_PROGRAMS = {68, 69, 71, 72, 73, 74, 75, 78, 79}
PIERCING_MELODY_PROGRAMS = {72, 74, 75, 78, 79}
# 若理論庫仍殘留刺耳主奏，編成時改成溫暖替代
SAFE_MELODY_FALLBACK = {
    72: 40,  # piccolo → violin
    74: 71,  # recorder → clarinet
    75: 46,  # pan flute → harp
    78: 24,  # whistle → nylon guitar
    79: 11,  # ocarina → vibraphone
}


def _fold_midi_note(note: int, low: int = 55, high: int = 76) -> int:
    """把音高折進 [low, high]，保留音名（十二平均律八度）。"""
    n = int(note)
    while n > high:
        n -= 12
    while n < low:
        n += 12
    return max(low, min(high, n))


def compute_song_structure(notes: list, bpm: float, target_seconds: int = 30) -> dict:
    """
    計算歌曲結構（前奏/主旋律小節數/重複次數/尾奏）。

    target_seconds 決定歌曲長度：
        30：只有副歌——主旋律以完整編制重複到約 30 秒
        60：中等長度，重複更多次
        90：完整歌曲——前段做成「主歌」（安靜編制），後段才是全編制副歌
    quiet_repeats 表示前面幾次主旋律用安靜編制（無鼓、長音貝斯、鋪底和聲）。
    """
    from app.audio.quantize import quantize_notes

    quantized = quantize_notes(notes, bpm, grid="1/8")
    beats_per_second = bpm / 60.0
    bar_duration = 4.0 / beats_per_second

    melody_end = max((n["end"] for n in quantized), default=0.0)
    melody_bars = max(1, math.ceil(melody_end / bar_duration - 1e-6))
    melody_bars = min(melody_bars, 16)  # 安全上限

    target = max(15.0, float(target_seconds))
    section_seconds = melody_bars * bar_duration
    available = target - 2 * bar_duration  # 扣掉前奏＋尾奏
    repeats = max(1, round(available / section_seconds))

    # 不要超過目標長度太多（容忍 15%）
    def total_seconds(rp):
        return (1 + melody_bars * rp + 1) * bar_duration
    while repeats > 1 and total_seconds(repeats) > target * 1.15:
        repeats -= 1

    # 90 秒的完整結構：前面的重複當「主歌」（安靜編制），醞釀到副歌
    if target_seconds >= 90 and repeats >= 3:
        quiet_repeats = max(1, repeats // 3)
    elif target_seconds >= 60 and repeats >= 4:
        quiet_repeats = 1
    else:
        quiet_repeats = 0

    return {
        "intro_bars": 1,
        "melody_bars": melody_bars,
        "repeats": repeats,
        "quiet_repeats": quiet_repeats,
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
    duration_seconds: int = 30,
) -> str:
    """
    生成完整的 MIDI 檔案（回傳檔案路徑）。
    seed 相同時，伴奏變化型與結構完全相同，方便重現。
    style 指定風格時，鼓/貝斯/和聲的變化型與樂器音色依樂理資料庫的風格定義挑選
    （例如搖籃曲：音樂盒主奏、無鼓；搖滾：電吉他、切分大鼓）。
    duration_seconds（30/60/90）決定歌曲長度與結構：30 秒＝副歌重複、
    90 秒＝完整歌曲（前段安靜主歌 → 全編制副歌）。
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
    from app.arrange.patterns import get_decoration_pattern
    from app.arrange.chords import get_chord_notes, select_chords_for_melody

    from app.theory.knowledge import get_style, pick_ensemble

    rng = random.Random(seed)
    style_cfg = get_style(style)
    decoration2 = None
    if style_cfg:
        # 指定風格：優先從該風格標籤的編制池抽一組（樂器更多變），
        # 若無標籤編制再退回風格內建的 program 清單。
        mood = rng.choice(style_cfg.get("moods") or ["bright"])
        ensemble = pick_ensemble(mood, rng, style=style)
        if style in (ensemble.get("styles") or []):
            melody_program = ensemble["melody_program"]
            harmony_program = ensemble["harmony_program"]
            decoration = ensemble.get("decoration")
            decoration2 = ensemble.get("decoration2")
        else:
            melody_program = rng.choice(style_cfg.get("melody_programs") or MELODY_PROGRAM_CHOICES)
            harmony_program = rng.choice(style_cfg.get("harmony_programs") or HARMONY_PROGRAM_CHOICES)
            deco_pool = style_cfg.get("decoration_choices") or (
                [style_cfg["decoration"]] if style_cfg.get("decoration") else []
            )
            decoration = rng.choice(deco_pool) if deco_pool else None
            deco2_pool = style_cfg.get("decoration2_choices") or (
                [style_cfg["decoration2"]] if style_cfg.get("decoration2") else []
            )
            decoration2 = rng.choice(deco2_pool) if deco2_pool else None
        drum_choices = style_cfg.get("drum_variations", [])
        drum_var = rng.choice(drum_choices) if drum_choices else None
        bass_var = rng.choice(style_cfg.get("bass_variations") or [0])
        harmony_var = rng.choice(style_cfg.get("harmony_variations") or [0])
        # 若風格禁用鼓，強制關閉（即使抽到有鼓的編制）
        if not drum_choices:
            drum_var = None
    else:
        # 自動：每次隨機挑一組「樂團編制」
        ensemble = pick_ensemble(None, rng)
        melody_program = ensemble["melody_program"]
        harmony_program = ensemble["harmony_program"]
        decoration = ensemble.get("decoration")
        decoration2 = ensemble.get("decoration2")
        drum_var = rng.randrange(NUM_DRUM_VARIATIONS) if ensemble.get("drums", True) else None
        bass_var = rng.randrange(NUM_BASS_VARIATIONS)
        harmony_var = rng.randrange(NUM_HARMONY_VARIATIONS)

    # 安全網：刺耳哨笛主奏改成溫暖樂器
    if melody_program in SAFE_MELODY_FALLBACK:
        melody_program = SAFE_MELODY_FALLBACK[melody_program]
    if decoration and decoration.get("program") in PIERCING_MELODY_PROGRAMS:
        decoration = {**decoration, "program": 71}
    if decoration2 and decoration2.get("program") in PIERCING_MELODY_PROGRAMS:
        decoration2 = {**decoration2, "program": 48}

    quantized_notes = quantize_notes(notes, bpm, grid="1/8")

    # 主奏是木管類時，素材旋律也折進較低音域
    if melody_program in WOODWIND_LIKE_PROGRAMS:
        for n in quantized_notes:
            n["midi"] = _fold_midi_note(n["midi"], low=55, high=74)
            n["velocity"] = min(int(n.get("velocity", 90)), 88)

    beats_per_second = bpm / 60.0
    bar_duration = 4.0 / beats_per_second

    # ---- 歌曲結構（與 compute_song_structure 一致） ----
    structure = compute_song_structure(notes, bpm, target_seconds=duration_seconds)
    melody_bars = structure["melody_bars"]
    INTRO_BARS = structure["intro_bars"]
    REPEATS = structure["repeats"]
    QUIET_REPEATS = structure["quiet_repeats"]
    OUTRO_BARS = structure["outro_bars"]

    # 超過旋律小節上限的音符裁掉
    cutoff = melody_bars * bar_duration
    quantized_notes = [n for n in quantized_notes if n["start"] < cutoff]
    for n in quantized_notes:
        n["end"] = min(n["end"], cutoff)

    # 沒有指定和弦時，依旋律內容挑最貼合的和弦（含 V→I 終止式）
    if chord_overrides is None:
        chord_overrides = select_chords_for_melody(
            quantized_notes, key, bpm, melody_bars,
            style=style, seed=seed,
        )
        print(
            f"[chords] style={style or 'auto'} → {'-'.join(chord_overrides)}",
            flush=True,
        )
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
    deco_track = MidiTrack()
    deco2_track = MidiTrack()
    for t in (melody_track, drums_track, bass_track, harmony_track, deco_track, deco2_track):
        mid.tracks.append(t)

    tempo = mido.bpm2tempo(bpm)
    melody_track.append(MetaMessage('set_tempo', tempo=tempo, time=0))

    melody_track.append(Message('program_change', program=melody_program, channel=0, time=0))
    bass_track.append(Message('program_change', program=BASS_PROGRAM, channel=1, time=0))
    harmony_track.append(Message('program_change', program=harmony_program, channel=2, time=0))
    if decoration:
        deco_track.append(Message('program_change', program=decoration.get("program", 0), channel=3, time=0))
    if decoration2:
        deco2_track.append(Message('program_change', program=decoration2.get("program", 0), channel=4, time=0))

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
    mel_high = 74 if melody_program in WOODWIND_LIKE_PROGRAMS else 81
    melody_events = [
        (
            n["start"],
            n["end"] - n["start"],
            _fold_midi_note(n["midi"], low=48, high=mel_high),
            max(20, min(127, int(max(70, min(110, n.get("velocity", 90))) * melody_gain))),
        )
        for n in full_melody
    ]
    write_track_events(melody_track, 0, melody_events)

    # ---- 伴奏軌：先收集全部事件，最後一次寫入 ----
    drum_all = []
    bass_all = []
    harmony_all = []
    deco_all = []
    deco2_all = []

    for bar in range(total_bars):
        bar_start = bar * bar_duration
        degree = bar_chords[bar]
        is_outro = bar >= outro_bar
        # 這個小節屬於第幾次主旋律重複；前面 QUIET_REPEATS 次是「主歌」（安靜編制）
        rep_idx = (bar - INTRO_BARS) // melody_bars if INTRO_BARS <= bar < outro_bar else -1
        is_quiet = 0 <= rep_idx < QUIET_REPEATS
        # 有主歌段時，前奏也跟著安靜，避免「前奏大聲→主歌突然安靜」的突兀感
        if QUIET_REPEATS > 0 and bar < INTRO_BARS:
            is_quiet = True
        # 段落開頭放 crash；段落結尾前一小節放過門
        has_crash = (bar in section_start_bars) or is_outro
        next_is_section = (bar + 1) in section_start_bars or (bar + 1) == outro_bar
        has_fill = next_is_section and not is_outro

        # 鼓（drum_var 為 None 表示此風格不用鼓，例如搖籃曲；主歌段不進鼓，副歌才全編制）
        if drum_var is None or is_quiet:
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

        # Bass（主歌段改長音根音，安靜鋪底）
        if is_outro:
            root = get_chord_notes(key, "I")[0] - 24
            bass_events = [{"time": 0.0, "note": root, "velocity": 80, "duration": bar_duration * 0.95}]
        else:
            bass_events = get_bass_pattern(degree, key, bar_duration, beats_per_bar=4,
                                           variation=1 if is_quiet else bass_var)
        bass_all.extend((bar_start + e["time"], e["duration"], e["note"], e["velocity"]) for e in bass_events)

        # 和聲（主歌段改長和弦鋪底）
        if is_outro:
            harmony_events = [
                {"time": 0.0, "note": n - 12, "velocity": 60, "duration": bar_duration * 0.95}
                for n in get_chord_notes(key, "I")
            ]
        else:
            harmony_events = get_harmony_pattern(degree, key, bar_duration,
                                                 variation=0 if is_quiet else harmony_var)
        harmony_all.extend((bar_start + e["time"], e["duration"], e["note"], e["velocity"]) for e in harmony_events)

        # 裝飾聲部 1／2（小提琴、長笛、豎笛、銅管等織體；主歌段不進，留給副歌）
        if decoration and not is_quiet:
            deco_type = "pad" if is_outro else decoration.get("type", "arp")
            deco_prog = decoration.get("program", 0)
            deco_events = get_decoration_pattern(degree, key, bar_duration, deco_type=deco_type)
            high = 74 if deco_prog in WOODWIND_LIKE_PROGRAMS else 76
            deco_all.extend(
                (
                    bar_start + e["time"],
                    e["duration"],
                    _fold_midi_note(e["note"], low=48, high=high),
                    e["velocity"],
                )
                for e in deco_events
            )
        if decoration2 and not is_quiet and not is_outro:
            deco2_prog = decoration2.get("program", 0)
            deco2_events = get_decoration_pattern(
                degree, key, bar_duration, deco_type=decoration2.get("type", "sustain")
            )
            high2 = 74 if deco2_prog in WOODWIND_LIKE_PROGRAMS else 76
            deco2_all.extend(
                (
                    bar_start + e["time"],
                    e["duration"],
                    _fold_midi_note(e["note"], low=48, high=high2),
                    e["velocity"],
                )
                for e in deco2_events
            )

    write_track_events(drums_track, 9, drum_all)
    write_track_events(bass_track, 1, bass_all)
    write_track_events(harmony_track, 2, harmony_all)
    write_track_events(deco_track, 3, deco_all)
    write_track_events(deco2_track, 4, deco2_all)

    for track in (melody_track, drums_track, bass_track, harmony_track, deco_track, deco2_track):
        track.append(MetaMessage('end_of_track', time=0))

    output_dir = "/tmp"
    os.makedirs(output_dir, exist_ok=True)
    midi_path = os.path.join(output_dir, "full.mid")
    mid.save(midi_path)

    return midi_path
