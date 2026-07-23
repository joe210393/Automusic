"""
MIDI 生成 - 將音符、和弦、Pattern 組合成完整的 MIDI 檔案
"""

import math
import mido
from mido import MidiFile, MidiTrack, Message, MetaMessage
import os
from typing import List, Optional


def generate_full_midi(
    notes: list,
    bpm: float,
    key: str,
    lyrics: dict = None,
    chord_overrides: Optional[List[str]] = None,
) -> str:
    """
    生成完整的 MIDI 檔案
    
    Args:
        notes: 旋律音符列表
        bpm: 節拍速度
        key: 調性
        lyrics: 歌詞（可選）
    
    Returns:
        MIDI 檔案路徑
    """
    from app.audio.quantize import quantize_notes
    from app.arrange.chords import infer_chords
    from app.arrange.patterns import get_drum_pattern, get_bass_pattern, get_harmony_pattern
    
    # 量化音符
    quantized_notes = quantize_notes(notes, bpm, grid="1/8")
    
    # 推斷和弦（先得到基礎 progression，用來估算長度與小節數）
    chords = infer_chords(quantized_notes, key, bpm, time_signature=(4, 4))

    # 若沒有指定 chord_overrides，預設使用固定 progression：I - vi - IV - V
    # 對應 C 調時就是 C - Am - F - G，不依賴 AI 或旋律分析。
    if chord_overrides is None:
        chord_overrides = ["I", "vi", "IV", "V"]
    
    # 建立 MIDI 檔案
    mid = MidiFile()
    mid.ticks_per_beat = 480
    
    # Track 0: Melody（旋律）
    melody_track = MidiTrack()
    mid.tracks.append(melody_track)
    
    # Track 1: Drums（鼓組）
    drums_track = MidiTrack()
    mid.tracks.append(drums_track)
    
    # Track 2: Bass（低音）
    bass_track = MidiTrack()
    mid.tracks.append(bass_track)
    
    # Track 3: Harmony（和聲）
    harmony_track = MidiTrack()
    mid.tracks.append(harmony_track)
    
    # 設定 tempo（使用 MetaMessage，寫在第一個 track 即可）
    tempo = mido.bpm2tempo(bpm)
    melody_track.append(MetaMessage('set_tempo', tempo=tempo, time=0))

    # 設定樂器音色（Program Change）：
    # - melody：預設樂器（通常是鋼琴）
    # - bass / harmony：使用吉他和弦感（Acoustic Guitar (nylon), GM #25 -> program 24）
    guitar_program = 24
    bass_track.append(Message('program_change', program=guitar_program, channel=1, time=0))
    harmony_track.append(Message('program_change', program=guitar_program, channel=2, time=0))
    
    # 添加旋律音符
    melody_current_time = 0
    for note in quantized_notes:
        start_ticks = int(note["start"] * mid.ticks_per_beat * bpm / 60)
        end_ticks = int(note["end"] * mid.ticks_per_beat * bpm / 60)
        duration_ticks = end_ticks - start_ticks
        
        # Note on（學生旋律放在背景：降低 velocity）
        delta_time = start_ticks - melody_current_time
        if delta_time < 0:
            delta_time = 0
        base_vel = note.get("velocity", 90)
        melody_vel = max(30, int(base_vel * 0.6))  # 降低一點，讓和弦較突出
        melody_track.append(
            Message(
                'note_on',
                channel=0,
                note=note["midi"],
                velocity=melody_vel,
                time=delta_time,
            )
        )
        melody_current_time = start_ticks
        
        # Note off
        melody_track.append(Message('note_off', channel=0, note=note["midi"], velocity=0, time=duration_ticks))
        melody_current_time = end_ticks
    
    # 添加鼓組、Bass 和和聲
    beats_per_second = bpm / 60.0
    bar_duration = 4.0 / beats_per_second
    
    # ---- 自動延長伴奏：目標長度 15~30 秒 ----
    # 先計算目前 progression 的長度
    if chords:
        original_num_bars = len(chords)
        original_duration = original_num_bars * bar_duration
    else:
        original_num_bars = 0
        original_duration = 0.0
    
    target_min_duration = 15.0  # 至少 15 秒
    target_max_duration = 30.0  # 最長約 30 秒，避免太長
    
    extended_chords = []
    if original_num_bars == 0:
        # 沒有任何和弦時，保守起見直接用 4 小節 I
        total_bars = 4
        for bar in range(total_bars):
            extended_chords.append(
                {
                    "bar": bar,
                    "chord": "I",
                    "start": bar * bar_duration,
                    "end": (bar + 1) * bar_duration,
                }
            )
    else:
        # 至少複製一次 progression，直到達到目標長度或上限
        total_bars = original_num_bars
        if original_duration < target_min_duration:
            # 需要額外幾個小節
            need_duration = target_min_duration - original_duration
            extra_bars = math.ceil(need_duration / bar_duration)
            
            # 避免超過最大長度
            max_extra_bars = math.floor(
                max(0.0, target_max_duration - original_duration) / bar_duration
            )
            extra_bars = max(0, min(extra_bars, max_extra_bars))
            total_bars = original_num_bars + extra_bars
        
        # 依序填滿 extended_chords
        for new_bar_index in range(total_bars):
            # 使用 chord_overrides 決定和弦級數（例如 ["I","vi","IV","V"]）
            src_chord = chord_overrides[new_bar_index % len(chord_overrides)]
            bar_start = new_bar_index * bar_duration
            bar_end = (new_bar_index + 1) * bar_duration
            extended_chords.append(
                {
                    "bar": new_bar_index,
                    "chord": src_chord,
                    "start": bar_start,
                    "end": bar_end,
                }
            )
        
        # 最後一小節強制回 I，讓結尾穩定
        extended_chords[-1]["chord"] = "I"
    
    chords = extended_chords
    
    # 為每個 track 獨立追蹤時間
    drums_current_time = 0
    bass_current_time = 0
    harmony_current_time = 0
    
    for chord_info in chords:
        bar = chord_info["bar"]
        chord_degree = chord_info["chord"]
        bar_start_time = chord_info["start"]
        
        # 轉換為 ticks
        bar_start_ticks = int(bar_start_time * mid.ticks_per_beat * bpm / 60)
        
        # 鼓組 Pattern
        drum_events = get_drum_pattern(bar_duration, beats_per_bar=4)
        for event in drum_events:
            event_time_ticks = int((bar_start_time + event["time"]) * mid.ticks_per_beat * bpm / 60)
            delta_time = max(0, event_time_ticks - drums_current_time)
            drums_track.append(Message('note_on', channel=9, note=event["note"], velocity=event["velocity"], time=delta_time))
            duration_ticks = int(event["duration"] * mid.ticks_per_beat * bpm / 60)
            drums_track.append(Message('note_off', channel=9, note=event["note"], velocity=0, time=duration_ticks))
            drums_current_time = event_time_ticks + duration_ticks
        
        # Bass Pattern
        bass_events = get_bass_pattern(chord_degree, key, bar_duration, beats_per_bar=4)
        for event in bass_events:
            event_time_ticks = int((bar_start_time + event["time"]) * mid.ticks_per_beat * bpm / 60)
            delta_time = max(0, event_time_ticks - bass_current_time)
            bass_track.append(Message('note_on', channel=1, note=event["note"], velocity=event["velocity"], time=delta_time))
            duration_ticks = int(event["duration"] * mid.ticks_per_beat * bpm / 60)
            bass_track.append(Message('note_off', channel=1, note=event["note"], velocity=0, time=duration_ticks))
            bass_current_time = event_time_ticks + duration_ticks
        
        # Harmony Pattern
        harmony_events = get_harmony_pattern(chord_degree, key, bar_duration)
        for event in harmony_events:
            event_time_ticks = int((bar_start_time + event["time"]) * mid.ticks_per_beat * bpm / 60)
            delta_time = max(0, event_time_ticks - harmony_current_time)
            harmony_track.append(Message('note_on', channel=2, note=event["note"], velocity=event["velocity"], time=delta_time))
            duration_ticks = int(event["duration"] * mid.ticks_per_beat * bpm / 60)
            harmony_track.append(Message('note_off', channel=2, note=event["note"], velocity=0, time=duration_ticks))
            harmony_current_time = event_time_ticks + duration_ticks
    
    # 為每個 track 加上 end_of_track，確保 MIDI 在各種播放器中都正常結束
    for track in (melody_track, drums_track, bass_track, harmony_track):
        track.append(MetaMessage('end_of_track', time=0))
    
    # 儲存 MIDI 檔案
    output_dir = "/tmp"
    os.makedirs(output_dir, exist_ok=True)
    midi_path = os.path.join(output_dir, "full.mid")
    mid.save(midi_path)
    
    return midi_path
