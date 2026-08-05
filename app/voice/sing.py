"""
歌聲合成（步驟 6）：
把使用者唸的歌詞錄音，依旋律的音高／節奏「唱」出來。

做法（避免 WORLD 聲碼器造成的魔音）：
1. 從聲紋估計使用者的說話音高，把旋律整段移到他唱得上去的音域
2. 每一句對應一組音符；句內依字數切音節
3. 每個音節用 ffmpeg（asetrate/atempo）做變調＋變速——保留原始說話音色，
   不像 WORLD 那樣把頻譜拆掉重建
4. 組成整軌後交給 Seed-VC 做神經音色轉換（見 neural_vc.py）
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def count_syllables(text: str) -> int:
    return len(CJK_RE.findall(text))


def load_mono(path: str, target_fs: int = 44100) -> np.ndarray:
    x, fs = sf.read(path, dtype="float64")
    if x.ndim > 1:
        x = x.mean(axis=1)
    if fs != target_fs:
        n_target = int(len(x) * target_fs / fs)
        x = np.interp(np.linspace(0, len(x) - 1, n_target), np.arange(len(x)), x)
    return x


def trim_silence(x: np.ndarray, fs: int, threshold_ratio: float = 0.06) -> np.ndarray:
    frame = max(1, int(fs * 0.02))
    n_frames = len(x) // frame
    if n_frames < 3:
        return x
    rms = np.array([
        float(np.sqrt(np.mean(x[i * frame:(i + 1) * frame] ** 2)))
        for i in range(n_frames)
    ])
    peak = rms.max()
    if peak <= 0:
        return x
    active = np.where(rms > peak * threshold_ratio)[0]
    if len(active) == 0:
        return x
    start = active[0] * frame
    end = min(len(x), (active[-1] + 1) * frame)
    return x[start:end]


def midi_to_freq(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def freq_to_midi(freq: float) -> float:
    return 69.0 + 12.0 * np.log2(max(freq, 1.0) / 440.0)


def estimate_speaker_midi(voiceprint_dir: Path, manifest: dict, fs: int = 44100) -> Optional[float]:
    """從聲紋錄音估計使用者說話的中位音高（MIDI）。"""
    try:
        import pyworld as pw
    except Exception:
        return None

    f0s = []
    lines = sorted(
        manifest.get("lines", []),
        key=lambda l: (0 if l.get("section") == "verse" else 1, l.get("index", 0)),
    )
    for line in lines:
        path = voiceprint_dir / line.get("filename", "")
        if not path.exists():
            continue
        try:
            x = trim_silence(load_mono(str(path), fs), fs)
        except Exception:
            continue
        if len(x) < fs // 5:
            continue
        f0, _ = pw.harvest(
            np.ascontiguousarray(x, dtype=np.float64), fs,
            frame_period=5.0, f0_floor=70.0, f0_ceil=500.0,
        )
        voiced = f0[f0 > 0]
        if len(voiced):
            f0s.append(voiced)

    if not f0s:
        return None
    all_f0 = np.concatenate(f0s)
    # 說話中位音高再往上 4 個半音，比較像「唱」而不是「唸」
    return float(freq_to_midi(float(np.median(all_f0))) + 4.0)


def fold_notes_to_speaker_range(notes: list, speaker_midi: Optional[float]) -> list:
    """
    把每個音符「折」進使用者唱得上去的八度（只加減 12 的倍數，音名不變）。
    這樣才不會跟伴奏差調——整段平移半音才是魔音主因之一。
    """
    if not notes:
        return notes
    center = float(speaker_midi) if speaker_midi is not None else 60.0
    low = max(48, center - 7)   # 約 ± 七度的舒適區
    high = min(76, center + 7)
    folded = []
    for n in notes:
        m = int(n["midi"])
        while m < low:
            m += 12
        while m > high:
            m -= 12
        # 若剛好在邊界外還是太遠，貼近中心八度
        if abs(m - center) > abs(m - 12 - center) and m - 12 >= 48:
            m -= 12
        if abs(m - center) > abs(m + 12 - center) and m + 12 <= 79:
            m += 12
        folded.append({**n, "midi": m})
    print(f"[sing] 人聲音域折疊至 MIDI {low:.0f}-{high:.0f}（使用者中心 ≈ {center:.0f}）")
    return folded


def _atempo_chain(ratio: float) -> str:
    """ffmpeg atempo 單次只接受 0.5~2.0，串接多個濾鏡覆蓋更大範圍。"""
    if ratio <= 0:
        ratio = 1.0
    filters = []
    r = ratio
    while r > 2.0:
        filters.append("atempo=2.0")
        r /= 2.0
    while r < 0.5:
        filters.append("atempo=0.5")
        r /= 0.5
    filters.append(f"atempo={r:.6f}")
    return ",".join(filters)


def pitch_and_stretch(x: np.ndarray, fs: int, semitones: float, target_dur: float) -> np.ndarray:
    """
    用 ffmpeg 變調＋變速（保留原始頻譜，遠比 WORLD 重建自然）。
    semitones: 要升／降幾個半音；target_dur: 目標秒數。
    """
    if len(x) < 32:
        return np.zeros(max(1, int(target_dur * fs)))

    src_dur = len(x) / fs
    tempo = src_dur / max(0.08, target_dur)  # >1 = 加速（變短）
    rate = 2.0 ** (semitones / 12.0)

    # asetrate 改變音高與速度；再 aresample 回 44100；atempo 把速度調回目標
    # 最終速度倍率 = rate * (tempo/rate) = tempo，音高倍率 = rate
    af = f"asetrate={fs * rate:.4f},aresample={fs},{_atempo_chain(tempo / rate)}"

    in_path = tempfile.mktemp(prefix="syl_in_", suffix=".wav")
    out_path = tempfile.mktemp(prefix="syl_out_", suffix=".wav")
    try:
        sf.write(in_path, x.astype(np.float32), fs)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", in_path, "-af", af, out_path],
            check=True, capture_output=True, timeout=60,
        )
        y, out_fs = sf.read(out_path, dtype="float64")
        if y.ndim > 1:
            y = y.mean(axis=1)
        if out_fs != fs:
            n_target = int(len(y) * fs / out_fs)
            y = np.interp(np.linspace(0, len(y) - 1, n_target), np.arange(len(y)), y)
    except Exception as e:
        print(f"[sing] ffmpeg 變調失敗，改用簡易重取樣：{e}")
        # 後備：只做變速（不變調），總比沒聲音好
        n_target = max(1, int(target_dur * fs))
        y = np.interp(np.linspace(0, len(x) - 1, n_target), np.arange(len(x)), x)
    finally:
        for p in (in_path, out_path):
            try:
                os.unlink(p)
            except Exception:
                pass

    n_target = max(1, int(target_dur * fs))
    if len(y) < n_target:
        y = np.pad(y, (0, n_target - len(y)))
    y = y[:n_target]

    # 淡入淡出避免咔噠聲
    fade = min(len(y) // 4, int(fs * 0.008))
    if fade > 0:
        y[:fade] *= np.linspace(0, 1, fade)
        y[-fade:] *= np.linspace(1, 0, fade)
    return y


def split_into_syllables(x: np.ndarray, n: int) -> list:
    if n <= 0 or len(x) == 0:
        return []
    seg = len(x) / n
    return [x[int(i * seg):int((i + 1) * seg)] for i in range(n)]


def estimate_src_midi(x: np.ndarray, fs: int) -> float:
    """估計一個音節原本的音高，用來算要移幾個半音。"""
    try:
        import pyworld as pw
        f0, _ = pw.harvest(
            np.ascontiguousarray(x, dtype=np.float64), fs,
            frame_period=5.0, f0_floor=70.0, f0_ceil=500.0,
        )
        voiced = f0[f0 > 0]
        if len(voiced) >= 3:
            return float(freq_to_midi(float(np.median(voiced))))
    except Exception:
        pass
    return 60.0  # 找不到就當 C4


def sing_line(x: np.ndarray, fs: int, notes: list) -> np.ndarray:
    """
    把一整句說話錄音，依字數切音節後逐音對到音符（ffmpeg 變調變速）。
    """
    total_dur = notes[-1]["end"]
    out = np.zeros(int(total_dur * fs) + 1)
    if not notes or len(x) < fs // 20:
        return out

    n_syl = max(len(notes), 1)
    # 音節數跟音符數對齊：字比音多就合併後面的字；音比字多就循環用音節
    syllables = split_into_syllables(trim_silence(x, fs), n_syl)
    if not syllables:
        return out

    for i, note in enumerate(notes):
        syl = syllables[i % len(syllables)]
        src_midi = estimate_src_midi(syl, fs)
        semitones = note["midi"] - src_midi
        # 單次變調超過 ±7 半音會明顯失真，拆成較溫和的偏移
        semitones = float(np.clip(semitones, -7.0, 7.0))
        dur = max(0.08, note["end"] - note["start"])
        y = pitch_and_stretch(syl, fs, semitones, dur)
        start = int(note["start"] * fs)
        end = min(start + len(y), len(out))
        if start >= len(out):
            continue
        out[start:end] += y[: end - start]

    peak = float(np.max(np.abs(out)))
    if peak > 0.95:
        out *= 0.95 / peak
    return out


def load_section_lines(voiceprint_dir: Path, manifest: dict, section: str, fs: int = 44100) -> list:
    result = []
    lines = [l for l in manifest.get("lines", []) if l.get("section") == section]
    lines.sort(key=lambda l: l.get("index", 0))
    for line in lines:
        path = voiceprint_dir / line.get("filename", "")
        n = count_syllables(line.get("text", ""))
        if n == 0 or not path.exists():
            continue
        try:
            x = trim_silence(load_mono(str(path), fs), fs)
        except Exception:
            continue
        if len(x) >= fs // 10:
            result.append((n, x))
    return result


def allocate_notes_to_lines(notes: list, syllable_counts: list) -> list:
    total_syll = sum(syllable_counts)
    n_notes = len(notes)
    if total_syll == 0 or n_notes == 0:
        return [[] for _ in syllable_counts]
    groups = []
    cum = 0
    prev_cut = 0
    for s in syllable_counts:
        cum += s
        cut = round(n_notes * cum / total_syll)
        groups.append(notes[prev_cut:cut])
        prev_cut = cut
    return groups


def build_vocal_track(
    notes: list,
    bpm: float,
    structure: dict,
    voiceprint_dir: Path,
    manifest: dict,
    total_samples: int,
    fs: int = 44100,
) -> Optional[np.ndarray]:
    """
    生成整首歌的人聲軌。會先依聲紋把旋律移到使用者音域。
    """
    from app.audio.quantize import quantize_notes

    verse_lines = load_section_lines(voiceprint_dir, manifest, "verse", fs)
    chorus_lines = load_section_lines(voiceprint_dir, manifest, "chorus", fs)
    if not verse_lines and not chorus_lines:
        return None
    if not verse_lines:
        verse_lines = chorus_lines
    if not chorus_lines:
        chorus_lines = verse_lines

    speaker_midi = estimate_speaker_midi(voiceprint_dir, manifest, fs)
    notes = fold_notes_to_speaker_range(notes, speaker_midi)

    quantized = quantize_notes(notes, bpm, grid="1/8")
    if not quantized:
        return None

    bar = structure["bar_duration"]
    intro = structure["intro_bars"]
    melody_bars = structure["melody_bars"]
    repeats = structure["repeats"]
    quiet = structure.get("quiet_repeats", 0)

    mix = np.zeros(total_samples)
    cache = {}

    for rep in range(repeats):
        section = "verse" if rep < quiet else "chorus"
        lines = verse_lines if section == "verse" else chorus_lines
        offset = (intro + rep * melody_bars) * bar
        groups = allocate_notes_to_lines(quantized, [n for n, _ in lines])
        for line_idx, ((_, audio), group) in enumerate(zip(lines, groups)):
            if not group:
                continue
            g_start = group[0]["start"]
            rel = [
                {"start": n["start"] - g_start, "end": n["end"] - g_start, "midi": n["midi"]}
                for n in group
            ]
            key = (section, line_idx, tuple((n["midi"], round(n["start"], 3), round(n["end"], 3)) for n in rel))
            if key not in cache:
                cache[key] = sing_line(audio, fs, rel)
            y = cache[key]
            start = int((offset + g_start) * fs)
            end = min(start + len(y), total_samples)
            if start >= total_samples:
                continue
            mix[start:end] += y[: end - start]

    peak = float(np.max(np.abs(mix)))
    if peak > 0.95:
        mix *= 0.95 / peak
    return mix


def apply_reverb(x: np.ndarray, fs: int = 44100) -> np.ndarray:
    """殘響在 Seed-VC 之後再加，避免干擾轉換。"""
    d1, d2 = int(0.09 * fs), int(0.17 * fs)
    rev = x.copy()
    rev[d1:] += x[:-d1] * 0.22
    rev[d2:] += x[:-d2] * 0.12
    peak = float(np.max(np.abs(rev)))
    if peak > 0.95:
        rev *= 0.95 / peak
    return rev
