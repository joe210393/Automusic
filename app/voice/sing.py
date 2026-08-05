"""
歌聲合成（步驟 6 的核心）——「整句演唱」法：
把使用者唸的一整句歌詞用 WORLD 聲碼器一次分析（不切碎，保留字與字的連音、
咬字與自然的音量起伏），再沿時間軸把整句「彎」到這一句對應的一組旋律音符上：
音高在音符邊界切換（帶滑音），長音加顫音，音符之間的空拍自動靜音。

歌詞配置跟真歌一樣：一個 4 小節的旋律段落分給該段落的 4 句歌詞
（依字數比例分配音符），主歌段唱主歌詞、副歌段唱副歌詞。
"""
import re
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

FRAME_PERIOD_MS = 5.0
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def count_syllables(text: str) -> int:
    """中文歌詞的音節數＝漢字數（標點、空白不算）。"""
    return len(CJK_RE.findall(text))


def load_mono(path: str, target_fs: int = 44100) -> np.ndarray:
    """讀 WAV 成單聲道 float64，必要時重取樣到 target_fs。"""
    x, fs = sf.read(path, dtype="float64")
    if x.ndim > 1:
        x = x.mean(axis=1)
    if fs != target_fs:
        n_target = int(len(x) * target_fs / fs)
        x = np.interp(
            np.linspace(0, len(x) - 1, n_target),
            np.arange(len(x)),
            x,
        )
    return x


def trim_silence(x: np.ndarray, fs: int, threshold_ratio: float = 0.06) -> np.ndarray:
    """去掉頭尾的安靜段（門檻＝峰值 RMS 的一定比例）。"""
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


def sing_line(x: np.ndarray, fs: int, notes: list) -> np.ndarray:
    """
    把「一整句」語音唱到一組音符上。

    x：唸這句歌詞的錄音（已去頭尾靜音）
    notes：[{"start","end","midi"}]，時間相對於這組的第一個音符起點

    作法：
    1. 整句一次 WORLD 分析（f0/頻譜包絡/非週期性）——不切碎，連音自然
    2. 音符 j（共 K 個）對應原句的第 j/K 段：建立逐幀的「源位置」映射
    3. f0 換成音符音高：音符間 40ms 滑音、長音顫音、無聲子音保持無聲
    4. 一次合成整句，空拍處淡出靜音，保留整句自然的能量起伏
    回傳長度 = 最後一個音符結束時間的波形。
    """
    import pyworld as pw

    x = np.ascontiguousarray(x, dtype=np.float64)
    total_dur = notes[-1]["end"]
    n_target = max(8, int(total_dur * 1000.0 / FRAME_PERIOD_MS))
    if len(x) < int(fs * 0.05) or not notes:
        return np.zeros(int(total_dur * fs))

    f0, t = pw.harvest(x, fs, frame_period=FRAME_PERIOD_MS, f0_floor=70.0, f0_ceil=800.0)
    sp = pw.cheaptrick(x, f0, t, fs)
    ap = pw.d4c(x, f0, t, fs)
    n_src = len(f0)
    K = len(notes)

    frame_time = np.arange(n_target) * FRAME_PERIOD_MS / 1000.0
    src_pos = np.full(n_target, -1.0)   # 每個目標幀取原句的哪一幀（-1＝空拍）
    f0_target = np.zeros(n_target)

    prev_freq = None
    for j, note in enumerate(notes):
        i0 = int(note["start"] * 1000.0 / FRAME_PERIOD_MS)
        i1 = min(n_target, int(note["end"] * 1000.0 / FRAME_PERIOD_MS))
        if i1 <= i0:
            continue
        n_frames = i1 - i0
        # 音符 j 對應原句第 j/K 段（比例映射：字數多就唱快、字數少就唱慢）
        s0 = (n_src - 1) * j / K
        s1 = (n_src - 1) * (j + 1) / K
        src_pos[i0:i1] = np.linspace(s0, s1, n_frames)

        freq = midi_to_freq(note["midi"])
        seg_t = frame_time[i0:i1] - frame_time[i0]
        freq_arr = np.full(n_frames, freq)
        # 滑音：從上一個音高在 40ms 內滑過來，比較像人聲
        if prev_freq is not None and prev_freq > 0:
            glide = np.clip(1.0 - seg_t / 0.04, 0.0, 1.0)
            freq_arr = freq * (prev_freq / freq) ** glide
        # 顫音：只加在 0.45 秒以上的長音，0.25 秒後淡入（5.3Hz、±20 音分）
        dur = note["end"] - note["start"]
        if dur > 0.45:
            ramp = np.clip((seg_t - 0.25) / 0.3, 0.0, 1.0)
            freq_arr = freq_arr * 2.0 ** ((20.0 / 1200.0) * np.sin(2 * np.pi * 5.3 * seg_t) * ramp)
        f0_target[i0:i1] = freq_arr
        prev_freq = freq

    in_note = src_pos >= 0
    # 空拍處的源位置用前一個有效值補（合成時再把振幅壓到 0）
    filled = src_pos.copy()
    last = 0.0
    for i in range(n_target):
        if filled[i] < 0:
            filled[i] = last
        else:
            last = filled[i]

    base = np.arange(n_src)
    sp_t = np.empty((n_target, sp.shape[1]))
    ap_t = np.empty((n_target, ap.shape[1]))
    for col in range(sp.shape[1]):
        sp_t[:, col] = np.interp(filled, base, sp[:, col])
        ap_t[:, col] = np.interp(filled, base, ap[:, col])

    # 有聲/無聲遮罩跟著映射：無聲子音（氣音）保持無聲才自然
    voiced_src = (f0 > 0).astype(float)
    if not voiced_src.any():
        voiced_src = np.ones(n_src)
    voiced_t = np.interp(filled, base, voiced_src) > 0.5
    f0_final = np.where(in_note & voiced_t, f0_target, 0.0)

    y = pw.synthesize(
        np.ascontiguousarray(f0_final),
        np.ascontiguousarray(sp_t),
        np.ascontiguousarray(ap_t),
        fs,
        frame_period=FRAME_PERIOD_MS,
    )

    # 空拍靜音（逐幀 gain 展開到取樣點，邊界 15ms 平滑）
    gain_frames = in_note.astype(float)
    edge = max(1, int(15 / FRAME_PERIOD_MS))
    kernel = np.ones(edge) / edge
    gain_smooth = np.convolve(gain_frames, kernel, mode="same")
    sample_idx = np.minimum(
        (np.arange(len(y)) / (fs * FRAME_PERIOD_MS / 1000.0)).astype(int),
        n_target - 1,
    )
    y = y * gain_smooth[sample_idx]

    # 整句響度統一（不逐字正規化，保留句內自然強弱）
    active = y[np.abs(y) > 1e-5]
    rms = float(np.sqrt(np.mean(active ** 2))) if len(active) else 0.0
    if rms > 1e-6:
        y = y * (0.12 / rms)
    y = np.clip(y, -0.98, 0.98)

    target_len = int(total_dur * fs)
    if len(y) < target_len:
        y = np.pad(y, (0, target_len - len(y)))
    return y[:target_len]


def load_section_lines(voiceprint_dir: Path, manifest: dict, section: str, fs: int = 44100) -> list:
    """載入某段落（verse/chorus）每一句的整句錄音：[(音節數, 波形), ...]。"""
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
    """
    把一個旋律段落的音符分給各句歌詞（連續分組，字數多的句子分到較多音符）。
    回傳與 syllable_counts 等長的 list，每個元素是音符子序列。
    """
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
    生成整首歌的人聲軌（與 generate_full_midi 完全相同的時間軸）。
    每次主旋律重複唱完該段落的所有句子（主歌段唱主歌詞、副歌段唱副歌詞），
    最後加一點殘響讓人聲融入伴奏。聲紋不足時回傳 None。
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

    quantized = quantize_notes(notes, bpm, grid="1/8")
    if not quantized:
        return None
    bar = structure["bar_duration"]
    intro = structure["intro_bars"]
    melody_bars = structure["melody_bars"]
    repeats = structure["repeats"]
    quiet = structure.get("quiet_repeats", 0)

    mix = np.zeros(total_samples)
    cache = {}  # 每個 section 的重複內容一樣，只需合成一次

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
            key = (section, line_idx)
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
    """簡單殘響（兩個延遲抽頭），讓人聲跟有殘響的伴奏融在一起。
    要在神經聲音轉換「之後」才加，殘響進模型會干擾轉換品質。"""
    d1, d2 = int(0.09 * fs), int(0.17 * fs)
    rev = x.copy()
    rev[d1:] += x[:-d1] * 0.28
    rev[d2:] += x[:-d2] * 0.16
    peak = float(np.max(np.abs(rev)))
    if peak > 0.95:
        rev *= 0.95 / peak
    return rev
