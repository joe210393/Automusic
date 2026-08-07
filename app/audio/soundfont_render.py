"""
高品質音色渲染：MuseScore GM 當底，主奏＋背景分軌用原聲取樣覆寫後混音。

單樂器 SF2（FreePats）preset 多在 000-000；Sonatina 管弦用自訂 program。
因此依 channel／program 拆成多個 stem，各自用對應 SF2 渲染再混在一起。

log 在本機：/tmp/automusic.log（不是 Zeabur 網址）。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from mido import MidiFile, MidiTrack, Message, MetaMessage

SOUNDFONTS_DIR = Path(__file__).resolve().parent.parent.parent / "soundfonts"

# GM program → (相對 soundfonts/ 的 SF2, 該庫內的 preset)
# 主奏與背景共用此表；鼓（ch9）永遠走底庫。
LAYER_PROGRAM_FONTS: Dict[int, Tuple[str, int]] = {
    # —— 主奏／鍵盤 ——
    0: ("leads/YDP-GrandPiano.sf2", 0),
    1: ("leads/YDP-GrandPiano.sf2", 0),
    2: ("leads/YDP-GrandPiano.sf2", 0),
    3: ("leads/YDP-GrandPiano.sf2", 0),
    4: ("leads/YDP-GrandPiano.sf2", 0),   # EP → 原聲鋼琴（背景墊底也較自然）
    5: ("leads/YDP-GrandPiano.sf2", 0),
    # —— 吉他 ——
    24: ("leads/NylonGuitar.sf2", 0),
    25: ("leads/SteelGuitar.sf2", 0),
    26: ("leads/NylonGuitar.sf2", 0),
    27: ("leads/SteelGuitar.sf2", 0),
    # —— 貝斯（背景）——
    32: ("leads/FingerBass.sf2", 0),
    33: ("leads/FingerBass.sf2", 0),
    34: ("leads/FingerBass.sf2", 0),
    35: ("leads/FingerBass.sf2", 0),
    # —— 豎琴 ——
    46: ("leads/ConcertHarp.sf2", 0),
    # —— 弦樂主奏／鋪底（Sonatina）——
    40: ("leads/Sonatina_Orchestra.sf2", 12),  # Violin Solo
    41: ("leads/Sonatina_Orchestra.sf2", 12),
    42: ("leads/Sonatina_Orchestra.sf2", 13),  # Cello Solo
    43: ("leads/Sonatina_Orchestra.sf2", 13),
    44: ("leads/Sonatina_Orchestra.sf2", 2),   # Tremolo → violins sustain
    45: ("leads/Sonatina_Orchestra.sf2", 3),   # Pizz
    48: ("leads/Sonatina_Orchestra.sf2", 2),   # String Ensemble → 1st Violins Sustain
    49: ("leads/Sonatina_Orchestra.sf2", 4),   # String Ensemble 2
    50: ("leads/Sonatina_Orchestra.sf2", 2),   # Synth Strings → violins
    51: ("leads/Sonatina_Orchestra.sf2", 8),   # Synth Strings 2 → celli
    # —— 銅管 ——
    56: ("leads/Sonatina_Orchestra.sf2", 27),
    57: ("leads/Sonatina_Orchestra.sf2", 31),
    60: ("leads/Sonatina_Orchestra.sf2", 29),
    61: ("leads/Sonatina_Orchestra.sf2", 28),  # Brass section → trumpet section
    # —— 薩克斯／木管 ——
    64: ("leads/TenorSax.sf2", 0),
    65: ("leads/TenorSax.sf2", 0),
    66: ("leads/TenorSax.sf2", 0),
    67: ("leads/TenorSax.sf2", 0),
    68: ("leads/Sonatina_Orchestra.sf2", 16),
    69: ("leads/Sonatina_Orchestra.sf2", 19),
    71: ("leads/Clarinet.sf2", 0),
    72: ("leads/Sonatina_Orchestra.sf2", 18),
    73: ("leads/Sonatina_Orchestra.sf2", 14),
    74: ("leads/Recorder.sf2", 0),
    75: ("leads/Recorder.sf2", 0),
    78: ("leads/Recorder.sf2", 0),
    79: ("leads/Recorder.sf2", 0),
}

# 向下相容舊名稱
LEAD_PROGRAM_FONTS = LAYER_PROGRAM_FONTS


def find_base_soundfont() -> Optional[str]:
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


def resolve_layer_soundfont(program: int) -> Optional[Tuple[str, int]]:
    entry = LAYER_PROGRAM_FONTS.get(program)
    if not entry:
        return None
    rel, preset = entry
    path = SOUNDFONTS_DIR / rel
    if not path.exists():
        return None
    return str(path), preset


def resolve_lead_soundfont(program: int) -> Optional[Tuple[str, int]]:
    return resolve_layer_soundfont(program)


def acoustic_lead_programs() -> set:
    """主奏／背景可覆寫的 GM program（本機檔案存在者）。"""
    out = set()
    for prog, (rel, _preset) in LAYER_PROGRAM_FONTS.items():
        if (SOUNDFONTS_DIR / rel).exists():
            out.add(prog)
    return out


def can_render_acoustic_locally() -> bool:
    """
    本機是否具備原聲向音色（不只 Docker 預設 FluidR3）。
    Zeabur 通常為 False → 應把 MIDI 渲染委託回 Mac（ngrok）。
    """
    if acoustic_lead_programs():
        return True
    base = find_base_soundfont() or ""
    name = Path(base).name
    return "MuseScore" in name or "GeneralUser" in name


def melody_program_from_midi(midi_path: str) -> int:
    mid = MidiFile(midi_path)
    for track in mid.tracks:
        for msg in track:
            if msg.type == "program_change" and getattr(msg, "channel", None) == 0:
                return int(msg.program)
    return 0


def _parse_midi(midi_path: str):
    """回傳 ticks_per_beat, tempo_msg, programs{ch:prog}, events[(abs_t, msg)]."""
    src = MidiFile(midi_path)
    programs: Dict[int, int] = {}
    events = []
    tempo_msg = None
    for track in src.tracks:
        t = 0
        for msg in track:
            t += msg.time
            if msg.is_meta:
                if msg.type == "set_tempo" and tempo_msg is None:
                    tempo_msg = msg
                continue
            if msg.type == "program_change":
                programs[int(msg.channel)] = int(msg.program)
            if msg.type in ("note_on", "note_off", "program_change", "control_change"):
                events.append((t, msg))
    events.sort(key=lambda x: x[0])
    return src.ticks_per_beat, tempo_msg, programs, events


def _build_stem_midi(
    ticks_per_beat: int,
    tempo_msg,
    channel_presets: Dict[int, int],
    events: list,
    *,
    remap_preset: bool,
) -> str:
    """
    建立只含指定 channel 的 MIDI。
    remap_preset=True 時用 channel_presets 的值當 program；
    False 時保留原始 program_change（給底庫 residual 用）。
    """
    mid = MidiFile(ticks_per_beat=ticks_per_beat)
    track = MidiTrack()
    mid.tracks.append(track)
    if tempo_msg is not None:
        track.append(tempo_msg.copy(time=0))
    # 開頭寫各 channel 的 program（原聲 stem 用 remap 後的 preset；底庫用原始 GM）
    for ch, preset in sorted(channel_presets.items()):
        track.append(Message("program_change", program=int(preset) % 128, channel=ch, time=0))

    wanted = set(channel_presets.keys())
    stem_events = []
    for abs_t, msg in events:
        ch = getattr(msg, "channel", None)
        if ch not in wanted:
            continue
        if msg.type == "program_change":
            continue  # 已在開頭寫過
        if msg.type in ("note_on", "note_off", "control_change"):
            stem_events.append((abs_t, msg))

    cur = 0
    for abs_t, msg in stem_events:
        delta = max(0, abs_t - cur)
        track.append(msg.copy(time=delta))
        cur = abs_t
    track.append(MetaMessage("end_of_track", time=0))

    fd, path = tempfile.mkstemp(suffix="_stem.mid")
    os.close(fd)
    mid.save(path)
    return path


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


def _mix_many(wav_gains: List[Tuple[str, float]], out_wav: str):
    import numpy as np
    import soundfile as sf

    if not wav_gains:
        raise RuntimeError("沒有可混音的 stem")

    mixed = None
    sr0 = None
    for path, gain in wav_gains:
        data, sr = sf.read(path, dtype="float32")
        if data.ndim == 1:
            data = np.stack([data, data], axis=1)
        if sr0 is None:
            sr0 = sr
            mixed = np.zeros_like(data)
        elif sr != sr0:
            raise RuntimeError(f"取樣率不一致：{sr} vs {sr0}")
        if len(data) > len(mixed):
            pad = np.zeros((len(data) - len(mixed), mixed.shape[1]), dtype=np.float32)
            mixed = np.concatenate([mixed, pad], axis=0)
        mixed[: len(data)] += data * gain

    peak = float(np.max(np.abs(mixed))) if mixed is not None and len(mixed) else 0.0
    if peak > 0.99:
        mixed *= 0.99 / peak
    sf.write(out_wav, mixed, sr0, subtype="PCM_16")


def _stem_gain(channels: set) -> float:
    """主旋律稍大聲，背景鋪底略收。"""
    if 0 in channels:
        return 0.92
    if channels <= {1}:  # 只有貝斯
        return 0.78
    return 0.70


def render_midi_to_wav(
    fluidsynth_bin: str,
    midi_path: str,
    wav_path: str,
    *,
    use_lead_overlay: bool = True,
) -> dict:
    """
    渲染 MIDI → WAV。
    主奏（ch0）＋背景（和聲／裝飾／貝斯等）若有原聲對應，分 stem 渲染後混音；
    鼓與未對應聲部走 MuseScore 底庫。
    """
    base = find_base_soundfont()
    if not base:
        raise FileNotFoundError("找不到 SoundFont 音色庫")

    ticks, tempo_msg, programs, events = _parse_midi(midi_path)
    # 有音符的 channel
    note_channels = {
        int(msg.channel)
        for _t, msg in events
        if msg.type in ("note_on", "note_off")
    }
    for ch in note_channels:
        programs.setdefault(ch, 0)

    # sf2_path -> {ch: preset}
    acoustic_groups: Dict[str, Dict[int, int]] = defaultdict(dict)
    residual: Dict[int, int] = {}

    for ch in sorted(note_channels | set(programs.keys())):
        prog = programs.get(ch, 0)
        # 鼓永遠底庫
        if ch == 9:
            residual[ch] = prog
            continue
        # 聲紋代唱時關掉主旋律原聲疊層（MIDI 旋律已壓低）
        if ch == 0 and not use_lead_overlay:
            residual[ch] = prog
            continue
        resolved = resolve_layer_soundfont(prog)
        if resolved:
            path, preset = resolved
            acoustic_groups[path][ch] = preset
        else:
            residual[ch] = prog

    tmp_paths: List[str] = []
    wav_gains: List[Tuple[str, float]] = []
    overlay_names: List[str] = []

    try:
        for sf2_path, ch_presets in acoustic_groups.items():
            mid = _build_stem_midi(
                ticks, tempo_msg, ch_presets, events, remap_preset=True
            )
            fd, wav = tempfile.mkstemp(suffix="_ac.wav")
            os.close(fd)
            tmp_paths.extend([mid, wav])
            _fluidsynth_render(fluidsynth_bin, [sf2_path], mid, wav, gain=0.72)
            wav_gains.append((wav, _stem_gain(set(ch_presets.keys()))))
            overlay_names.append(
                f"{Path(sf2_path).name}:ch{sorted(ch_presets.keys())}"
            )

        if residual:
            mid = _build_stem_midi(
                ticks, tempo_msg, residual, events, remap_preset=False
            )
            # 確保 residual 有原始 program_change
            fd, wav = tempfile.mkstemp(suffix="_base.wav")
            os.close(fd)
            tmp_paths.extend([mid, wav])
            _fluidsynth_render(fluidsynth_bin, [base], mid, wav, gain=0.7)
            wav_gains.append((wav, 0.72 if 9 in residual else 0.68))

        if not wav_gains:
            # 極端情況：整曲直接底庫
            _fluidsynth_render(fluidsynth_bin, [base], midi_path, wav_path, gain=0.7)
            mode = "base_only"
        else:
            _mix_many(wav_gains, wav_path)
            mode = "layered" if acoustic_groups else "base_only"

    finally:
        for p in tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    melody_prog = programs.get(0, 0)
    info = {
        "base_soundfont": base,
        "lead_soundfont": next(
            (p for p, chs in acoustic_groups.items() if 0 in chs), None
        ),
        "melody_program": melody_prog,
        "mode": mode,
        "overlays": overlay_names,
    }
    print(
        f"[render-audio] 音色：mode={mode} program={melody_prog} "
        f"base={Path(base).name} overlays={overlay_names or '-'}",
        flush=True,
    )
    return info


# 舊 API 保留（測試／相容）
def split_melody_and_accomp(midi_path: str, lead_preset: int = 0) -> Tuple[str, str]:
    ticks, tempo_msg, programs, events = _parse_midi(midi_path)
    melody = _build_stem_midi(
        ticks, tempo_msg, {0: lead_preset}, events, remap_preset=True
    )
    residual = {ch: prog for ch, prog in programs.items() if ch != 0}
    for _t, msg in events:
        if msg.type in ("note_on", "note_off") and msg.channel != 0:
            residual.setdefault(msg.channel, 0)
    accomp = _build_stem_midi(
        ticks, tempo_msg, residual, events, remap_preset=False
    )
    return melody, accomp
