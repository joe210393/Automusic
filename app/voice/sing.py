"""
歌聲合成（步驟 6 的核心）：
把使用者唸歌詞的錄音（步驟 5 的聲紋），逐字切成音節，
用 WORLD 聲碼器把每個音節移調到旋律音符的音高、拉伸到音符長度，
一顆一顆貼回歌曲時間軸，就變成「用你的聲音唱出這首歌」。

中文一個字＝一個音節，所以「唸歌詞」的錄音可以直接對應到旋律音符。
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


def split_line_into_syllables(x: np.ndarray, fs: int, n_syllables: int) -> list:
    """
    把一句話的錄音切成 n 個音節。
    中文語速相當平均，去頭尾靜音後等分即可（比 onset 偵測穩定得多）。
    """
    x = trim_silence(x, fs)
    if n_syllables <= 0 or len(x) < fs // 20:
        return []
    seg_len = len(x) / n_syllables
    return [
        x[int(i * seg_len):int((i + 1) * seg_len)]
        for i in range(n_syllables)
    ]


def midi_to_freq(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def synth_syllable_at_pitch(
    syllable: np.ndarray,
    fs: int,
    midi_note: int,
    duration: float,
) -> np.ndarray:
    """
    用 WORLD 把一個音節改成指定音高與長度：
    1. 分解成音高軌 f0／頻譜包絡 sp／非週期性 ap（sp/ap 保留使用者的音色＝聲紋）
    2. 時間軸線性拉伸到音符長度
    3. f0 換成音符頻率（保留無聲子音），長音加一點顫音比較像唱歌
    """
    import pyworld as pw

    syllable = np.ascontiguousarray(syllable, dtype=np.float64)
    if len(syllable) < int(fs * 0.02):
        return np.zeros(int(duration * fs))

    f0, t = pw.harvest(syllable, fs, frame_period=FRAME_PERIOD_MS, f0_floor=70.0, f0_ceil=800.0)
    sp = pw.cheaptrick(syllable, f0, t, fs)
    ap = pw.d4c(syllable, f0, t, fs)

    n_src = len(f0)
    n_target = max(4, int(duration * 1000.0 / FRAME_PERIOD_MS))
    src_idx = np.linspace(0, n_src - 1, n_target)

    # 頻譜與非週期性沿時間軸插值（音色不變、長度改變）
    base = np.arange(n_src)
    sp_t = np.empty((n_target, sp.shape[1]))
    ap_t = np.empty((n_target, ap.shape[1]))
    for j in range(sp.shape[1]):
        sp_t[:, j] = np.interp(src_idx, base, sp[:, j])
        ap_t[:, j] = np.interp(src_idx, base, ap[:, j])

    # 有聲/無聲遮罩也跟著拉伸：子音維持無聲才自然
    voiced_mask = np.interp(src_idx, base, (f0 > 0).astype(float)) > 0.5
    if not voiced_mask.any():
        voiced_mask = np.ones(n_target, dtype=bool)  # 整段都偵測不到音高就全部當有聲

    freq = midi_to_freq(midi_note)
    tt = np.arange(n_target) * FRAME_PERIOD_MS / 1000.0
    # 顫音：0.18 秒後淡入，5.3Hz、約 ±22 音分
    vib_ramp = np.clip((tt - 0.18) / 0.25, 0.0, 1.0)
    vibrato = 2.0 ** ((22.0 / 1200.0) * np.sin(2 * np.pi * 5.3 * tt) * vib_ramp)
    f0_t = np.where(voiced_mask, freq * vibrato, 0.0)

    y = pw.synthesize(
        np.ascontiguousarray(f0_t),
        np.ascontiguousarray(sp_t),
        np.ascontiguousarray(ap_t),
        fs,
        frame_period=FRAME_PERIOD_MS,
    )

    # 統一響度＋頭尾 10ms 淡入淡出，避免爆音
    rms = float(np.sqrt(np.mean(y ** 2)))
    if rms > 1e-6:
        y = y * (0.12 / rms)
    y = np.clip(y, -0.98, 0.98)
    fade = min(len(y) // 4, int(fs * 0.01))
    if fade > 0:
        y[:fade] *= np.linspace(0, 1, fade)
        y[-fade:] *= np.linspace(1, 0, fade)
    return y


def load_syllable_bank(voiceprint_dir: Path, manifest: dict, section: str, fs: int = 44100) -> list:
    """
    把某個段落（verse/chorus）所有句子的錄音切成音節清單（依句序、字序排列）。
    """
    bank = []
    lines = [l for l in manifest.get("lines", []) if l.get("section") == section]
    lines.sort(key=lambda l: l.get("index", 0))
    for line in lines:
        path = voiceprint_dir / line.get("filename", "")
        n = count_syllables(line.get("text", ""))
        if n == 0 or not path.exists():
            continue
        try:
            x = load_mono(str(path), fs)
        except Exception:
            continue
        bank.extend(s for s in split_line_into_syllables(x, fs, n) if len(s) > 0)
    return bank


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
    生成整首歌的人聲軌（與 generate_full_midi 完全相同的時間軸）：
    主歌段（quiet repeats）唱主歌歌詞、副歌段唱副歌歌詞，音節循環使用。
    回傳 float64 波形；聲紋不足時回傳 None。
    """
    from app.audio.quantize import quantize_notes

    verse_bank = load_syllable_bank(voiceprint_dir, manifest, "verse", fs)
    chorus_bank = load_syllable_bank(voiceprint_dir, manifest, "chorus", fs)
    if not verse_bank and not chorus_bank:
        return None
    if not verse_bank:
        verse_bank = chorus_bank
    if not chorus_bank:
        chorus_bank = verse_bank

    quantized = quantize_notes(notes, bpm, grid="1/8")
    bar = structure["bar_duration"]
    intro = structure["intro_bars"]
    melody_bars = structure["melody_bars"]
    repeats = structure["repeats"]
    quiet = structure.get("quiet_repeats", 0)

    mix = np.zeros(total_samples)
    counters = {"verse": 0, "chorus": 0}
    cache = {}

    for rep in range(repeats):
        section = "verse" if rep < quiet else "chorus"
        bank = verse_bank if section == "verse" else chorus_bank
        offset = (intro + rep * melody_bars) * bar
        for n in quantized:
            idx = counters[section] % len(bank)
            counters[section] += 1
            duration = max(0.08, n["end"] - n["start"])
            key = (section, idx, n["midi"], round(duration, 3))
            if key not in cache:
                cache[key] = synth_syllable_at_pitch(bank[idx], fs, n["midi"], duration)
            y = cache[key]
            start = int((offset + n["start"]) * fs)
            end = min(start + len(y), total_samples)
            if start >= total_samples:
                continue
            mix[start:end] += y[: end - start]

    peak = float(np.max(np.abs(mix)))
    if peak > 0.95:
        mix *= 0.95 / peak
    return mix
