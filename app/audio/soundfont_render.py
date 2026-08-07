"""
高品質音色渲染：MuseScore GM 當底，主奏用原聲取樣覆寫。

單樂器 SF2（FreePats）preset 多在 000-000；Sonatina 管弦則用自訂 program。
因此拆出主旋律軌，寫入對應 preset 後單獨渲染，再混回伴奏。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from mido import MidiFile, MidiTrack, Message, MetaMessage

SOUNDFONTS_DIR = Path(__file__).resolve().parent.parent.parent / "soundfonts"
LEADS_DIR = SOUNDFONTS_DIR / "leads"

# GM program → (相對 soundfonts/ 的 SF2, 該庫內的 preset program)
# Sonatina presets：12 Violin Solo, 13 Cello Solo, 14 Flute Solo, 16 Oboe,
# 18 Piccolo, 27 Trumpet Solo, 29 Horn Solo …
LEAD_PROGRAM_FONTS: Dict[int, Tuple[str, int]] = {
    # 鋼琴
    0: ("leads/YDP-GrandPiano.sf2", 0),
    1: ("leads/YDP-GrandPiano.sf2", 0),
    2: ("leads/YDP-GrandPiano.sf2", 0),
    3: ("leads/YDP-GrandPiano.sf2", 0),
    # 吉他
    24: ("leads/NylonGuitar.sf2", 0),
    25: ("leads/SteelGuitar.sf2", 0),
    26: ("leads/NylonGuitar.sf2", 0),   # jazz guitar → nylon
    27: ("leads/SteelGuitar.sf2", 0),   # clean electric → steel acoustic
    # 豎琴
    46: ("leads/ConcertHarp.sf2", 0),
    # 弦樂主奏（Sonatina）
    40: ("leads/Sonatina_Orchestra.sf2", 12),  # Violin
    41: ("leads/Sonatina_Orchestra.sf2", 12),  # Viola → violin solo
    42: ("leads/Sonatina_Orchestra.sf2", 13),  # Cello
    43: ("leads/Sonatina_Orchestra.sf2", 13),
    # 銅管主奏
    56: ("leads/Sonatina_Orchestra.sf2", 27),  # Trumpet
    57: ("leads/Sonatina_Orchestra.sf2", 31),  # Trombone
    60: ("leads/Sonatina_Orchestra.sf2", 29),  # French Horn
    # 薩克斯風
    64: ("leads/TenorSax.sf2", 0),
    65: ("leads/TenorSax.sf2", 0),
    66: ("leads/TenorSax.sf2", 0),
    67: ("leads/TenorSax.sf2", 0),
    # 木管
    68: ("leads/Sonatina_Orchestra.sf2", 16),  # Oboe
    69: ("leads/Sonatina_Orchestra.sf2", 19),  # English Horn
    71: ("leads/Clarinet.sf2", 0),
    72: ("leads/Sonatina_Orchestra.sf2", 18),  # Piccolo
    73: ("leads/Sonatina_Orchestra.sf2", 14),  # Flute
    74: ("leads/Recorder.sf2", 0),
    75: ("leads/Recorder.sf2", 0),             # pan flute → recorder
    78: ("leads/Recorder.sf2", 0),             # whistle
    79: ("leads/Recorder.sf2", 0),             # ocarina
}


def find_base_soundfont() -> Optional[str]:
    """優先 MuseScore_General（原聲感較佳），再退回 GeneralUser 等。"""
    if os.getenv("SOUNDFONT_PATH"):
        p = Path(os.getenv("SOUNDFONT_PATH"))
        if p.exists():
            return str(p)

    preferred = [
        SOUNDFONTS_DIR / "MuseScore_General.sf3",
        SOUNDFONTS_DIR / "MuseScore_General.sf2",
        SOUNDFONTS_DIR / "GeneralUserGS.sf2",
    ]
    for p in preferred:
        if p.exists():
            return str(p)

    if SOUNDFONTS_DIR.exists():
        for p in sorted(SOUNDFONTS_DIR.glob("*.sf3")) + sorted(SOUNDFONTS_DIR.glob("*.sf2")):
            return str(p)

    for p in (
        Path("/usr/share/sounds/sf2/FluidR3_GM.sf2"),
        Path("/usr/share/sounds/sf2/default-GM.sf2"),
    ):
        if p.exists():
            return str(p)
    return None


def resolve_lead_soundfont(program: int) -> Optional[Tuple[str, int]]:
    """回傳 (絕對路徑, preset) 或 None。"""
    entry = LEAD_PROGRAM_FONTS.get(program)
    if not entry:
        return None
    rel, preset = entry
    path = SOUNDFONTS_DIR / rel
    if not path.exists():
        return None
    return str(path), preset


def acoustic_lead_programs() -> set:
    """本機實際裝得到的原聲主奏 GM program 集合。"""
    out = set()
    for prog, (rel, _preset) in LEAD_PROGRAM_FONTS.items():
        if (SOUNDFONTS_DIR / rel).exists():
            out.add(prog)
    return out


def melody_program_from_midi(midi_path: str) -> int:
    mid = MidiFile(midi_path)
    for track in mid.tracks:
        for msg in track:
            if msg.type == "program_change" and getattr(msg, "channel", None) == 0:
                return int(msg.program)
    return 0


def split_melody_and_accomp(midi_path: str, lead_preset: int = 0) -> Tuple[str, str]:
    """
    拆成：
      - melody.mid：只含 channel 0，program = lead_preset
      - accomp.mid：其餘聲部，不含主旋律音
    """
    src = MidiFile(midi_path)
    melody = MidiFile(ticks_per_beat=src.ticks_per_beat)
    accomp = MidiFile(ticks_per_beat=src.ticks_per_beat)
    m_track = MidiTrack()
    a_track = MidiTrack()
    melody.tracks.append(m_track)
    accomp.tracks.append(a_track)

    tempo_set = False
    for track in src.tracks:
        for msg in track:
            if msg.type == "set_tempo" and not tempo_set:
                m_track.append(msg.copy(time=0))
                a_track.append(msg.copy(time=0))
                tempo_set = True
                break
        if tempo_set:
            break

    m_track.append(Message("program_change", program=int(lead_preset) % 128, channel=0, time=0))

    abs_events = []
    for track in src.tracks:
        t = 0
        for msg in track:
            t += msg.time
            if msg.is_meta:
                if msg.type in ("end_of_track", "set_tempo"):
                    continue
                abs_events.append((t, "meta", msg))
            else:
                abs_events.append((t, "ch", msg))
    abs_events.sort(key=lambda x: x[0])

    def _flush(track: MidiTrack, events: list):
        cur = 0
        for abs_t, _kind, msg in events:
            delta = max(0, abs_t - cur)
            track.append(msg.copy(time=delta))
            cur = abs_t
        track.append(MetaMessage("end_of_track", time=0))

    m_events = []
    a_events = []
    for abs_t, kind, msg in abs_events:
        if kind == "meta":
            continue
        ch = getattr(msg, "channel", None)
        if ch == 0:
            if msg.type in ("note_on", "note_off"):
                m_events.append((abs_t, kind, msg))
        else:
            a_events.append((abs_t, kind, msg))

    _flush(m_track, m_events)
    _flush(a_track, a_events)

    fd_m, melody_path = tempfile.mkstemp(suffix="_melody.mid")
    fd_a, accomp_path = tempfile.mkstemp(suffix="_accomp.mid")
    os.close(fd_m)
    os.close(fd_a)
    melody.save(melody_path)
    accomp.save(accomp_path)
    return melody_path, accomp_path


def _fluidsynth_render(
    fluidsynth_bin: str,
    soundfonts: List[str],
    midi_path: str,
    wav_path: str,
    gain: float = 0.7,
    timeout: int = 300,
):
    cmd = [
        fluidsynth_bin, "-ni",
        "-F", wav_path,
        "-r", "44100",
        "-g", str(gain),
        "-R", "1",
        "-C", "1",
        *soundfonts,
        midi_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)


def _mix_wavs(accomp_wav: str, melody_wav: str, out_wav: str,
              accomp_gain: float = 0.72, melody_gain: float = 0.95):
    import numpy as np
    import soundfile as sf

    acc, sr_a = sf.read(accomp_wav, dtype="float32")
    mel, sr_m = sf.read(melody_wav, dtype="float32")
    if sr_a != sr_m:
        raise RuntimeError(f"取樣率不一致：accomp {sr_a} vs melody {sr_m}")
    if acc.ndim == 1:
        acc = np.stack([acc, acc], axis=1)
    if mel.ndim == 1:
        mel = np.stack([mel, mel], axis=1)

    n = max(len(acc), len(mel))
    mix = np.zeros((n, 2), dtype=np.float32)
    mix[: len(acc)] += acc * accomp_gain
    mix[: len(mel)] += mel * melody_gain
    peak = float(np.max(np.abs(mix))) if n else 0.0
    if peak > 0.99:
        mix *= 0.99 / peak
    sf.write(out_wav, mix, sr_a, subtype="PCM_16")


def render_midi_to_wav(
    fluidsynth_bin: str,
    midi_path: str,
    wav_path: str,
    *,
    use_lead_overlay: bool = True,
) -> dict:
    """
    渲染 MIDI → WAV。
    若主奏 program 有對應原聲 SF2，則分軌渲染後混音；否則單次用底音色庫。
    """
    base = find_base_soundfont()
    if not base:
        raise FileNotFoundError("找不到 SoundFont 音色庫")

    program = melody_program_from_midi(midi_path)
    lead = resolve_lead_soundfont(program) if use_lead_overlay else None

    if not lead:
        _fluidsynth_render(fluidsynth_bin, [base], midi_path, wav_path, gain=0.7)
        info = {
            "base_soundfont": base,
            "lead_soundfont": None,
            "melody_program": program,
            "mode": "base_only",
        }
        print(f"[render-audio] 音色：mode=base_only program={program} base={Path(base).name}", flush=True)
        return info

    lead_path, lead_preset = lead
    melody_mid, accomp_mid = split_melody_and_accomp(midi_path, lead_preset=lead_preset)
    fd_m, melody_wav = tempfile.mkstemp(suffix="_melody.wav")
    fd_a, accomp_wav = tempfile.mkstemp(suffix="_accomp.wav")
    os.close(fd_m)
    os.close(fd_a)
    try:
        _fluidsynth_render(fluidsynth_bin, [lead_path], melody_mid, melody_wav, gain=0.75)
        _fluidsynth_render(fluidsynth_bin, [base], accomp_mid, accomp_wav, gain=0.7)
        _mix_wavs(accomp_wav, melody_wav, wav_path)
    finally:
        for p in (melody_mid, accomp_mid, melody_wav, accomp_wav):
            try:
                os.unlink(p)
            except OSError:
                pass

    info = {
        "base_soundfont": base,
        "lead_soundfont": lead_path,
        "melody_program": program,
        "mode": "lead_overlay",
    }
    print(
        f"[render-audio] 音色：mode=lead_overlay program={program} "
        f"base={Path(base).name} lead={Path(lead_path).name} preset={lead_preset}",
        flush=True,
    )
    return info
